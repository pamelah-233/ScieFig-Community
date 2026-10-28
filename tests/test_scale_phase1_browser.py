from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from core.image_browser import ScaleBarState, _resolve_um_per_px, render_snapshot
from core.models.fluoro_panel import FluorescencePanel
from core.models.project import Project
from core.utils.i18n import tr
from ui.panels.image_browser_workspace import ImageBrowserWorkspace
from ui.widgets.fluoro_canvas import FluorescenceCanvas


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
    def __init__(self, images: dict[Path, FakeBrowserImage]) -> None:
        self.images = images
        self.load_calls: list[Path] = []
        self.snapshot_calls = 0

    def discover_image_files(self, folder: str | Path) -> list[Path]:
        root = Path(folder)
        return sorted((path for path in self.images if path.parent == root), key=lambda path: path.name.casefold())

    def load_browser_image(self, path: str | Path) -> FakeBrowserImage:
        resolved = Path(path)
        self.load_calls.append(resolved)
        return self.images[resolved]

    def load_browser_thumbnail(self, path: str | Path, max_size: QSize = QSize(176, 132)) -> QImage:
        return _rgb_to_qimage(self.images[Path(path)].rgb).scaled(max_size, Qt.AspectRatioMode.KeepAspectRatio)

    def rgb_to_qimage(self, rgb: np.ndarray) -> QImage:
        return _rgb_to_qimage(rgb)

    def render_snapshot(self, image, show_scale_bar, length_um, manual_um_per_px=None, *, scale_bar_state=None):
        self.snapshot_calls += 1
        del show_scale_bar, length_um, manual_um_per_px, scale_bar_state
        return _rgb_to_qimage(image.rgb)


def _workspace(core: FakeBrowserCore) -> ImageBrowserWorkspace:
    return ImageBrowserWorkspace(
        loader=core.load_browser_image,
        discover=core.discover_image_files,
        thumbnail_loader=core.load_browser_thumbnail,
        snapshot=core.render_snapshot,
        to_qimage=core.rgb_to_qimage,
    )


def _flush(workspace: ImageBrowserWorkspace, app: QApplication) -> None:
    workspace.thread_pool.waitForDone(2000)
    app.processEvents()
    workspace.thread_pool.waitForDone(2000)
    app.processEvents()


def _write_dummy(path: Path, payload: bytes = b"source") -> Path:
    path.write_bytes(payload)
    return path


class _FakeFluoroPanel:
    panel_uuid = 'browser-scale-test'
    style: dict = {}
    analysis: dict = {}

    def __init__(self, image: dict, data: np.ndarray) -> None:
        self._image = image
        self._data = data

    def active_image(self):
        return self._image

    def image_data(self, _uid: str):
        return self._data


def test_browser_resolve_delegates_shared_calibration_without_fabricating() -> None:
    metadata = {"pixel_size_um": 0.5}
    assert _resolve_um_per_px(metadata, None) == pytest.approx(0.5)
    assert metadata == {"pixel_size_um": 0.5}
    assert _resolve_um_per_px({"pixel_size_x_um": 0.2, "pixel_size_y_um": 0.4}, None) == pytest.approx(0.2)
    assert _resolve_um_per_px({"pixel_size_y_um": 0.4}, None) is None
    assert _resolve_um_per_px({}, None) is None
    assert _resolve_um_per_px({}, 0.0) is None
    assert _resolve_um_per_px({}, 1.25) == pytest.approx(1.25)
    with pytest.raises(ValueError, match="positive"):
        _resolve_um_per_px({}, -0.2)


def test_uncalibrated_add_scale_does_not_fabricate_one_and_keeps_error(tmp_path) -> None:
    app = _app()
    source = _write_dummy(tmp_path / "plain.png")
    core = FakeBrowserCore({source: FakeBrowserImage(source, _rgb(80, 60), metadata={})})
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
        assert not workspace.error_label.isHidden()
        assert workspace.error_label.text()
        assert "uncalibrat" in workspace.error_label.text().casefold() or "µm/px" in workspace.error_label.text() or "um/px" in workspace.error_label.text().casefold()
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_transfer_emits_original_paths_and_structured_payload_without_baked_png(tmp_path) -> None:
    app = _app()
    first = _write_dummy(tmp_path / "a.png", b"a-source")
    second = _write_dummy(tmp_path / "b.png", b"b-source")
    images = {
        first: FakeBrowserImage(first, _rgb(80, 60), metadata={"pixel_size_um": 0.5}),
        second: FakeBrowserImage(second, _rgb(90, 70), metadata={"pixel_size_um": 0.25}),
    }
    core = FakeBrowserCore(images)
    workspace = _workspace(core)
    legacy: list[list[str]] = []
    structured: list[list[dict]] = []
    workspace.send_to_fluorescence_requested.connect(legacy.append)
    workspace.send_to_fluorescence_transfer_requested.connect(structured.append)
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        workspace.thumbnail_list.item(0).setSelected(True)
        workspace.um_per_px_spin.setValue(0.8)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.length_spin.setValue(12.0)
        workspace.thumbnail_list.setCurrentRow(1)
        _flush(workspace, app)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.thumbnail_list.item(1).setSelected(True)
        snapshot_before = core.snapshot_calls
        workspace.send_selected_to_fluorescence()
        _flush(workspace, app)
        assert core.snapshot_calls == snapshot_before
        assert legacy and [Path(path) for path in legacy[-1]] == [first, second]
        assert not any(Path(path).parent == workspace.transfer_dir for path in legacy[-1])
        assert first.read_bytes() == b"a-source"
        assert second.read_bytes() == b"b-source"
        payloads = structured[-1]
        assert [item["path"] for item in payloads] == [str(first), str(second)]
        assert payloads[0]["calibration"]["calibration_source"] == "manual"
        assert payloads[0]["calibration"]["um_per_px"] == pytest.approx(0.8)
        assert payloads[0]["display"]["visible"] is True
        assert payloads[0]["display"]["length_um"] == pytest.approx(12.0)
        assert "center_x_px" in payloads[0]["display"]
        assert "center_x_px" not in payloads[0]["calibration"]
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_browser_state_roundtrip_via_project_deepcopy() -> None:
    project = Project()
    assert project.browser_workspace_state == {}
    payload = {
        "folder": "C:/images",
        "images": {
            "C:/images/a.png": {
                "display": {"visible": True, "length_um": 18.0, "center_x_px": 9.0, "line_y_px": 11.0},
                "manual_um_per_px": 0.4,
            }
        },
    }
    project.browser_workspace_state = payload
    dumped = project.to_dict()
    payload["images"]["C:/images/a.png"]["display"]["length_um"] = 99.0
    assert dumped["browser_workspace_state"]["images"]["C:/images/a.png"]["display"]["length_um"] == pytest.approx(18.0)
    restored = Project.from_dict(dumped)
    assert restored.browser_workspace_state["images"]["C:/images/a.png"]["manual_um_per_px"] == pytest.approx(0.4)
    legacy = dict(dumped)
    legacy.pop("browser_workspace_state")
    assert Project.from_dict(legacy).browser_workspace_state == {}


def test_workspace_serialize_restore_preserves_display_and_manual(tmp_path) -> None:
    app = _app()
    source = _write_dummy(tmp_path / "cell.png")
    core = FakeBrowserCore({source: FakeBrowserImage(source, _rgb(32, 24), metadata={"pixel_size_um": 0.5})})
    workspace = _workspace(core)
    other = _workspace(core)
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        workspace.um_per_px_spin.setValue(0.33)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.length_spin.setValue(22.0)
        payload = workspace.serialize_browser_scale_states()
        other.restore_browser_scale_states(payload)
        restored = other.scale_bar_state_for(source)
        assert restored.length_um == pytest.approx(22.0)
        assert restored.visible is True
        assert other._manual_calibrations[str(source)] == pytest.approx(0.33)
    finally:
        workspace.close()
        other.close()
        workspace.deleteLater()
        other.deleteLater()
        app.processEvents()


def test_main_window_wires_structured_transfer_once_and_keeps_path_import_api(monkeypatch, tmp_path) -> None:
    app = _app()
    imported: list[list[str]] = []
    transferred: list[list[dict]] = []

    def capture_import(self, paths):
        imported.append(list(paths))

    def capture_transfer(self, payloads):
        transferred.append(list(payloads))
        self._transfer_module = self.active_module

    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "import_fluorescence_images", capture_import)
    monkeypatch.setattr(MainWindow, "import_browser_fluorescence_transfer", capture_transfer)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.set_module("viewer")
        workspace = window.image_browser_workspace
        payload = [{"path": str(tmp_path / "a.czi"), "calibration": {"calibrated": False}, "display": {"visible": False}}]
        workspace.send_to_fluorescence_requested.emit([str(tmp_path / "a.czi")])
        workspace.send_to_fluorescence_transfer_requested.emit(payload)
        app.processEvents()
        assert imported == []
        assert transferred == [payload]
        assert window.active_module == "viewer"
        assert window._transfer_module == "viewer"
        window.import_fluorescence_images([str(tmp_path / "plain.png")])
        assert imported == [[str(tmp_path / "plain.png")]]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_import_work_applies_browser_transfer_after_import() -> None:
    from ui.main_window import MainWindow

    class Panel:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def apply_browser_scale_transfer(self, image_uid: str, payload: dict) -> None:
            self.calls.append((image_uid, payload))

    panel = Panel()
    record = {"uid": "img-1", "source_path": "C:/synthetic/a.czi"}
    payload = {"path": "C:/synthetic/a.czi", "calibration": {"um_per_px": 0.2}, "display": {"visible": True}}
    MainWindow._apply_browser_scale_transfer_after_import(panel, record, {"C:/synthetic/a.czi": payload})
    assert panel.calls == [("img-1", payload)]


def test_fluoro_canvas_hides_unknown_line_uses_display_line_px_and_browser_overlay() -> None:
    app = _app()
    data = np.zeros((1, 40, 60), dtype=np.uint8)
    before = data.copy()
    channels = [{"index": 0, "name": "C", "visible": True, "pseudocolor": "#FF0000", "display_min": 0, "display_max": 255}]
    image = {
        "uid": "img1",
        "view_mode": "merge",
        "channels": channels,
        "metadata": {"width_px": 60, "height_px": 40},
        "measurement_scale_bar": {
            "enabled": True,
            "length_um": 50.0,
            "um_per_px": 0.0,
            "assumed": True,
            "calibrated": False,
            "calibration_source": "assumed",
            "line_px": {"x1": 0.0, "y1": 10.0, "x2": 40.0, "y2": 10.0},
            "display_line_px": {},
        },
        "browser_scale_bar": {
            "visible": True,
            "length_um": 10.0,
            "um_per_px": 0.5,
            "center_x_px": 20.0,
            "line_y_px": 30.0,
            "line_color": "#FFFFFF",
            "text_color": "#FFFFFF",
            "line_thickness_px": 4.0,
            "font_family": "Arial",
            "font_size_px": 12.0,
            "bold": False,
            "italic": False,
        },
    }
    canvas = FluorescenceCanvas()
    try:
        canvas.current_step = 1
        canvas.panel = _FakeFluoroPanel(image, data)
        canvas.refresh()
        markers = [str(item.data(0) or "") for item in canvas.scene.items()]
        assert "fluoro_measurement_scale_line" not in markers
        assert "fluoro_browser_scale_bar" in markers
        assert np.array_equal(data, before)

        image["measurement_scale_bar"] = {
            "enabled": True,
            "length_um": 10.0,
            "um_per_px": 0.5,
            "assumed": False,
            "calibrated": True,
            "calibration_source": "manual",
            "line_px": {"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 2.0},
            "display_line_px": {"x1": 5.0, "y1": 8.0, "x2": 25.0, "y2": 8.0},
        }
        canvas.refresh()
        assert not any(item.data(0) == 'fluoro_measurement_scale_line' for item in canvas.scene.items())
        canvas.set_roi_mode('scale_line')
        canvas.refresh()
        lines = [item for item in canvas.scene.items() if str(item.data(0) or "") == "fluoro_measurement_scale_line"]
        assert lines
        line = lines[0].line()
        assert line.y1() == pytest.approx(line.y2())
        assert abs(line.x2() - line.x1()) > abs(3.0 - 1.0)
        assert np.array_equal(data, before)
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def test_line_profile_uses_distance_px_axis_when_um_absent() -> None:
    app = _app()
    canvas = FluorescenceCanvas()
    try:
        rows = [
            {
                "distance_um": None,
                "distance_px": [0.0, 10.0, 20.0],
                "intensity": [1.0, 2.0, 3.0],
                "channel_name": "C1",
                "distance_unit": "px",
            }
        ]
        figure = canvas._build_line_profile_figure(rows, {})
        xlabel = figure.axes[0].get_xlabel().casefold()
        assert "px" in xlabel
        assert "um" not in xlabel
        canvas.draw_line_profile_chart(rows)
        labels = [item.toPlainText() for item in canvas.scene.items() if hasattr(item, "toPlainText")]
        assert any("px" in text.casefold() for text in labels)
        assert not any(text == "Distance (um)" for text in labels)
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def test_render_snapshot_still_blocks_uncalibrated_and_does_not_mutate_source(tmp_path) -> None:
    app = _app()
    source = np.full((12, 16, 3), [18, 24, 32], dtype=np.uint8)
    before = source.copy()
    image = FakeBrowserImage(tmp_path / "plain.png", source, metadata={})
    snapshot = render_snapshot(image, False, length_um=50.0)
    assert app is not None
    assert np.array_equal(source, before)
    assert snapshot.width() == 16
    with pytest.raises(ValueError, match="calibration"):
        render_snapshot(image, True, length_um=50.0)
    assert np.array_equal(source, before)


def test_transfer_preserves_visited_calibration_and_defers_unvisited(tmp_path) -> None:
    app = _app()
    first = _write_dummy(tmp_path / "a.png", b"a-source")
    second = _write_dummy(tmp_path / "b.png", b"b-source")
    images = {
        first: FakeBrowserImage(first, _rgb(80, 60), metadata={"pixel_size_um": 0.5, "pixel_size_x_um": 0.5, "pixel_size_y_um": 0.5}),
        second: FakeBrowserImage(second, _rgb(90, 70), metadata={"pixel_size_um": 0.25}),
    }
    core = FakeBrowserCore(images)
    workspace = _workspace(core)
    structured: list[list[dict]] = []
    workspace.send_to_fluorescence_transfer_requested.connect(structured.append)
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.thumbnail_list.item(0).setSelected(True)
        workspace.thumbnail_list.item(1).setSelected(True)
        workspace.send_selected_to_fluorescence()
        payloads = structured[-1]
        assert [item["path"] for item in payloads] == [str(first), str(second)]
        assert payloads[0]["calibration"]["calibrated"] is True
        assert payloads[0]["calibration"]["um_per_px"] == pytest.approx(0.5)
        assert "calibration" not in payloads[1]
        assert first.read_bytes() == b"a-source"
        assert second.read_bytes() == b"b-source"
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_restore_reopens_folder_keeps_edits_and_missing_folder_does_not_wipe(tmp_path) -> None:
    app = _app()
    source = _write_dummy(tmp_path / "cell.png")
    core = FakeBrowserCore({source: FakeBrowserImage(source, _rgb(32, 24), metadata={"pixel_size_um": 0.5})})
    workspace = _workspace(core)
    other = _workspace(core)
    missing = _workspace(core)
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        workspace.um_per_px_spin.setValue(0.33)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.length_spin.setValue(22.0)
        payload = workspace.serialize_browser_scale_states()
        other.restore_browser_scale_states(payload)
        _flush(other, app)
        assert other._folder == tmp_path
        assert other.scale_bar_state_for(source).length_um == pytest.approx(22.0)
        assert other._manual_calibrations[str(source)] == pytest.approx(0.33)
        other.open_folder(tmp_path)
        assert other.scale_bar_state_for(source).length_um == pytest.approx(22.0)
        gone = deepcopy(payload)
        gone["folder"] = str(tmp_path / "missing-folder")
        missing.restore_browser_scale_states(gone)
        assert missing.scale_bar_state_for(source).length_um == pytest.approx(22.0)
        assert missing._manual_calibrations[str(source)] == pytest.approx(0.33)
    finally:
        workspace.close()
        other.close()
        missing.close()
        workspace.deleteLater()
        other.deleteLater()
        missing.deleteLater()
        app.processEvents()


def test_apply_image_rereads_metadata_when_manual_is_none(tmp_path) -> None:
    app = _app()
    source = _write_dummy(tmp_path / "cell.png")
    image = FakeBrowserImage(source, _rgb(32, 24), metadata={"pixel_size_um": 0.5})
    core = FakeBrowserCore({source: image})
    workspace = _workspace(core)
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        assert workspace.scale_bar_state_for(source).um_per_px == pytest.approx(0.5)
        workspace._manual_calibrations[str(source)] = None
        image.metadata["pixel_size_um"] = 0.8
        image.metadata["pixel_size_x_um"] = 0.8
        workspace._apply_image(image)
        assert workspace.scale_bar_state_for(source).um_per_px == pytest.approx(0.8)
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_sfig_roundtrip_and_dirty_fingerprint_excludes_browser_state(tmp_path, monkeypatch) -> None:
    from ui.main_window import MainWindow

    app = _app()
    source = _write_dummy(tmp_path / "cell.png")
    core = FakeBrowserCore({source: FakeBrowserImage(source, _rgb(32, 24), metadata={"pixel_size_um": 0.4})})
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        workspace = window.image_browser_workspace
        workspace._load = core.load_browser_image
        workspace._discover = core.discover_image_files
        workspace._thumbnail_loader = core.load_browser_thumbnail
        workspace._snapshot = core.render_snapshot
        workspace._to_qimage = core.rgb_to_qimage
        window._capture_clean_project_baseline()
        assert window.is_project_dirty() is False
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        workspace.length_spin.setValue(31.0)
        workspace.scale_bar_checkbox.setChecked(True)
        assert window.is_project_dirty() is True
        before_import_fp = window._microscopy_import_project_fingerprint(window.project)
        window._store_browser_workspace_state()
        assert window._microscopy_import_project_fingerprint(window.project) == before_import_fp
        sfig = tmp_path / "browser.sfig"
        window.project.save_sfig(sfig)
        loaded = Project.load_sfig(sfig)
        restored = _workspace(core)
        restored.restore_browser_scale_states(loaded.browser_workspace_state)
        _flush(restored, app)
        assert restored._folder == tmp_path
        assert restored.scale_bar_state_for(source).length_um == pytest.approx(31.0)
        restored.close()
        restored.deleteLater()
        window.new_project()
        assert window.image_browser_workspace._scale_bar_states == {}
        assert window.project.browser_workspace_state == {}
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_preview_uses_shared_scale_and_skips_duplicate_auto_bar() -> None:
    app = _app()
    data = np.zeros((1, 40, 60), dtype=np.uint8)
    before = data.copy()
    channels = [{"index": 0, "name": "C", "visible": True, "pseudocolor": "#FF0000", "display_min": 0, "display_max": 255}]
    image = {
        "uid": "img1",
        "view_mode": "merge",
        "channels": channels,
        "metadata": {"width_px": 60, "height_px": 40, "pixel_size_um": 0.25, "pixel_size_x_um": 0.25, "pixel_size_y_um": 0.25},
        "measurement_scale_bar": {
            "enabled": True,
            "length_um": 10.0,
            "um_per_px": 0.25,
            "um_per_px_x": 0.25,
            "assumed": False,
            "calibrated": True,
            "calibration_source": "embedded",
            "line_px": {"x1": 0.0, "y1": 10.0, "x2": 40.0, "y2": 10.0},
        },
        "browser_scale_bar": {
            "visible": True,
            "length_um": 10.0,
            "um_per_px": 9.9,
            "center_x_px": 20.0,
            "line_y_px": 30.0,
            "line_color": "#FFFFFF",
            "text_color": "#FFFFFF",
            "line_thickness_px": 4.0,
            "font_family": "Arial",
            "font_size_px": 12.0,
            "bold": False,
            "italic": False,
        },
        "rois": [{"roi_id": "keep-me", "type": "rectangle", "x": 1, "y": 1, "w": 4, "h": 4}],
    }
    canvas = FluorescenceCanvas()
    try:
        canvas.current_step = 1
        canvas.panel = _FakeFluoroPanel(image, data)
        canvas.refresh()
        markers = [str(item.data(0) or "") for item in canvas.scene.items()]
        assert "fluoro_measurement_scale_line" not in markers
        assert "fluoro_browser_scale_bar" in markers
        state = canvas._browser_display_scale_bar_state(image)
        assert state.um_per_px == pytest.approx(0.25)
        assert image["rois"][0]["roi_id"] == "keep-me"
        assert np.array_equal(data, before)
        image["measurement_scale_bar"]["calibration_source"] = "manual_line"
        image["measurement_scale_bar"]["display_line_px"] = {"x1": 5.0, "y1": 8.0, "x2": 25.0, "y2": 8.0}
        canvas.refresh()
        assert not any(item.data(0) == 'fluoro_measurement_scale_line' for item in canvas.scene.items())
        canvas.set_roi_mode('scale_line')
        canvas.refresh()
        assert any(str(item.data(0) or "") == "fluoro_measurement_scale_line" for item in canvas.scene.items())
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def test_layout_bakes_browser_bar_and_legacy_bar_uses_current_calibration() -> None:
    app = _app()
    data = np.zeros((1, 40, 60), dtype=np.uint8)
    before = data.copy()
    channels = [{"index": 0, "name": "C", "visible": True, "pseudocolor": "#FF0000", "display_min": 0, "display_max": 255}]
    image = {
        "uid": "img1",
        "view_mode": "merge",
        "layout_mode": "merge_only",
        "channels": channels,
        "metadata": {"width_px": 60, "height_px": 40, "pixel_size_um": 0.5, "pixel_size_x_um": 0.5},
        "measurement_scale_bar": {
            "enabled": True,
            "length_um": 10.0,
            "um_per_px": 0.5,
            "um_per_px_x": 0.5,
            "assumed": False,
            "calibrated": True,
            "calibration_source": "embedded",
        },
        "scale_bar": {"length_um": 10.0, "length_px": 999.0, "show_text": True, "color": "#FFFFFF"},
        "browser_scale_bar": {
            "visible": True,
            "length_um": 10.0,
            "um_per_px": 0.5,
            "center_x_px": 20.0,
            "line_y_px": 30.0,
            "line_color": "#00FF00",
            "text_color": "#00FF00",
            "line_thickness_px": 6.0,
            "font_family": "Arial",
            "font_size_px": 12.0,
            "bold": False,
            "italic": False,
        },
    }
    helper = FluorescencePanel(panel_id="Figure1B")
    canvas = FluorescenceCanvas()
    panel = _FakeFluoroPanel(image, data)
    panel._draw_browser_scale_bar_on_image = helper._draw_browser_scale_bar_on_image
    try:
        canvas.current_step = 4
        canvas.panel = panel
        canvas.refresh()
        assert np.array_equal(data, before)
        markers = [str(item.data(0) or "") for item in canvas.scene.items()]
        assert "fluoro_scale_bar" not in markers
        image["browser_scale_bar"]["visible"] = False
        canvas.refresh()
        lines = [item for item in canvas.scene.items() if str(item.data(0) or "") == "fluoro_scale_bar" and str(item.data(3) or "") == "line"]
        assert lines
        line = lines[0].line()
        expected = 10.0 / 0.5
        assert abs((line.x2() - line.x1()) - expected) < abs((line.x2() - line.x1()) - 999.0)
        assert np.array_equal(data, before)
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def test_brightfield_apply_does_not_rewrite_history(tmp_path, monkeypatch) -> None:
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QMessageBox
    import ui.main_window as main_window_module
    from ui.main_window import MainWindow

    app = _app()
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 200, 200))
    path = tmp_path / "scale_apply.png"
    assert image.save(str(path))
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.set_module("brightfield")
        panel = window._active_brightfield_panel()
        imported = panel.import_image(path)
        called: list[int] = []
        monkeypatch.setattr(panel, "rescale_ai_object_count_measurements", lambda *args, **kwargs: called.append(1))
        window.data_panel.brightfield_he_fiber_scale_um_per_px_spin.setValue(0.5)
        window.apply_brightfield_measurement_scale()
        assert called == []
        assert window.statusBar().currentMessage() == tr("scale_history_uses_previous_calibration")
        window.refresh_brightfield_measurement_scale_results()
        assert called == [1]
        called.clear()
        window.reset_brightfield_measurement_scale()
        assert called == []
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
