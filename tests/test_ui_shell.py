from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPoint, QPointF, QRect, Qt, QUrl
from PyQt6.QtGui import QCloseEvent, QColor, QImage, QImageWriter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QFrame, QGroupBox, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget
import pytest

from core.ai_pack.runtime import AIBackendUnavailableError, ModelNotCachedError
from core.models import BrightfieldPanel, ExternalImagePanel, IHCPanel
from core.utils.i18n import tr
from core.utils.image_formats import BRIGHTFIELD_IMAGE_SUFFIXES, IHC_IMAGE_SUFFIXES
import ui.main_window as main_window_module
from ui.main_window import MainWindow
from ui.module_selector import MODULES
from ui.panels.brightfield_workspace import BrightfieldWorkspace
from ui.panels.fluorescence_workspace import FluorescenceWorkspace
from ui.panels.ihc_workspace import IHCWorkspace
from ui.widgets.canvas_zoom import ZOOM_STEP
from ui.widgets.preview_canvas import SelectableImageLabel
from ui.widgets.style_lab_workspace import StyleLabWorkspace
from ui.widgets.style_sidebar import StyleSidebar


class _AvailableScreen:
    def __init__(self, rect: QRect) -> None:
        self._rect = QRect(rect)

    def availableGeometry(self) -> QRect:
        return QRect(self._rect)


class _ScreenBoundMainWindow(MainWindow):
    def __init__(self, resources_dir: Path, available_rect: QRect) -> None:
        self._available_screen = _AvailableScreen(available_rect)
        super().__init__(resources_dir=resources_dir)

    def screen(self):
        return self._available_screen


class _DropEvent:
    def __init__(self, mime_data: QMimeData) -> None:
        self._mime_data = mime_data
        self.accepted = False

    def mimeData(self) -> QMimeData:
        return self._mime_data

    def acceptProposedAction(self) -> None:
        self.accepted = True


def _wheel_event() -> QWheelEvent:
    return QWheelEvent(
        QPointF(4.0, 4.0),
        QPointF(4.0, 4.0),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


def _scene_rect_fits_view(view, rect) -> bool:
    mapped = view.mapFromScene(rect).boundingRect()
    return mapped.width() <= view.viewport().width() + 2 and mapped.height() <= view.viewport().height() + 2


def _scene_rect_centered_in_view(view, rect, tolerance: int = 10) -> bool:
    mapped = view.mapFromScene(rect).boundingRect()
    viewport = view.viewport().rect()
    return (
        abs(mapped.center().x() - viewport.center().x()) <= tolerance
        and abs(mapped.center().y() - viewport.center().y()) <= tolerance
    )


def _scene_rect_centered_in_composer_visible_area(canvas, rect, tolerance: int = 10) -> bool:
    mapped = canvas.view.mapFromScene(rect).boundingRect()
    visible = canvas._visible_viewport_rect()
    return (
        abs(mapped.center().x() - visible.center().x()) <= tolerance
        and abs(mapped.center().y() - visible.center().y()) <= tolerance
    )


def _close_widget(widget: QWidget, app: QApplication) -> None:
    if isinstance(widget, MainWindow):
        widget._capture_clean_project_baseline()
    widget.close()
    widget.deleteLater()
    app.processEvents()


def _wait_for_analysis(window: MainWindow, key: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    assert app is not None
    while window._analysis_job_service.is_busy(key):
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {key} analysis")


def _wait_for_import(window: MainWindow, module_id: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    assert app is not None
    while window._analysis_job_service.is_busy(f"import:{module_id}"):
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {module_id} import")
        time.sleep(0.005)
    app.processEvents()


@pytest.fixture(autouse=True)
def _wait_for_async_microscopy_imports(monkeypatch):
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


def _patch_message_box_question(monkeypatch, response):
    calls = []

    def fake_question(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(main_window_module.QMessageBox, "question", fake_question)
    return calls


def _fake_brightfield_ai_analysis(count: int = 3) -> dict:
    areas = [10 + index for index in range(count)]
    centroids = [(float(index + 1), float(index + 2)) for index in range(count)]
    return {
        "ai_object_mask": [[0, 1], [2, 3]],
        "ai_object_count": count,
        "ai_object_areas_px": areas,
        "ai_object_centroids": centroids,
        "results": {
            "ai_object_count": count,
            "ai_object_total_area_px": sum(areas),
            "ai_object_mean_area_px": sum(areas) / max(1, count),
            "backend": "cellpose_cyto3",
        },
        "analysis_metadata": {
            "analysis_kind": "brightfield_ai_object_count",
            "model_sha256": "sha256:70cd701c225490dc63e35595070354a8100b0e243135749e63a263aa93cdacb9",
        },
        "analysis_warnings": [
            "Research model: restricted commercial-use status",
            "AI-assisted object count uses 256x256 resized inference",
            "Non-square images use direct resize before ONNX inference",
        ],
    }


def _write_synthetic_ihc_png(path: Path) -> Path:
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


def _write_synthetic_brightfield_he_png(path: Path) -> Path:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 246, 246))

    for y in range(1, 6):
        for x in range(2, 6):
            image.setPixelColor(x, y, QColor(235, 170, 190))

    assert image.save(str(path))
    return path


def _write_synthetic_he_fiber_area_png(path: Path) -> Path:
    image = QImage(120, 90, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 241, 244))
    for y in range(16, 44):
        for x in range(12, 42):
            image.setPixelColor(x, y, QColor(215, 74, 132))
    for y in range(20, 48):
        for x in range(56, 90):
            image.setPixelColor(x, y, QColor(220, 82, 138))
    for y in range(58, 72):
        for x in range(8, 108):
            image.setPixelColor(x, y, QColor(232, 184, 205))
    assert image.save(str(path))
    return path


def _write_tall_preview_png(path: Path) -> Path:
    image = QImage(96, 240, QImage.Format.Format_RGB32)
    image.fill(QColor(248, 248, 248))

    for y in range(36, 204):
        for x in range(20, 76):
            if (x + y) % 4:
                image.setPixelColor(x, y, QColor(232, 170, 192))
            else:
                image.setPixelColor(x, y, QColor(165, 110, 160))

    assert image.save(str(path))
    return path


def _mime_for(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def test_main_window_can_be_created_and_switch_modules() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    for module_id in ("qpcr", "wb", "fluoro", "ihc", "composer"):
        window.set_module(module_id)
        assert window.active_module == module_id

    window.set_module("brightfield")
    assert window.active_module == "brightfield"

    assert len(window.project.panels) == 6
    assert any(isinstance(panel, IHCPanel) for panel in window.project.panels)
    assert any(isinstance(panel, BrightfieldPanel) for panel in window.project.panels)
    _close_widget(window, app)


def test_module_selector_includes_independent_ihc_entry() -> None:
    assert [module_id for module_id, _label, _icon in MODULES] == ["qpcr", "wb", "fluoro", "ihc", "brightfield", "viewer", "composer", "stylelab"]
    assert [label for _module_id, label, _icon in MODULES] == ["qPCR", "WB", "Fluorescence", "IHC", "Brightfield", "Image Browser", "Composer", "Style Lab"]


def test_image_browser_module_wires_canvas_sidebar_stage_tools_and_export(monkeypatch, tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    export_calls: list[str] = []
    monkeypatch.setattr(window.image_browser_workspace, "export_snapshot", lambda path: export_calls.append(str(path)) or True)
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(tmp_path / "native.png"), "PNG (*.png)"),
    )
    try:
        assert window.module_selector.button_for_module("viewer") is not None
        window.module_selector.button_for_module("viewer").click()
        app.processEvents()

        assert window.active_module == "viewer"
        assert window.canvas_stack.currentWidget() is window.image_browser_workspace.canvas
        assert window.v9_shell._right_widget is window.image_browser_workspace.side_panel
        assert window.v9_shell._stage_buttons == []
        assert window.image_browser_workspace.canvas.zoom_controls.fit_button.isVisibleTo(window)
        assert window.image_browser_workspace.choose_folder_button.isVisibleTo(window)
        assert window.module_selector.button_for_module("viewer").toolTip() == "Image Browser"
        assert not window.is_project_dirty()
        window.image_browser_workspace._cached_image = type("LoadedImage", (), {"path": tmp_path / "loaded.png"})()

        captured: dict[str, object] = {}

        def fake_exec(self, *args, **kwargs):
            captured["menu"] = self
            return None

        monkeypatch.setattr(QMenu, "exec", fake_exec)
        monkeypatch.setattr(QMenu, "popup", fake_exec)
        window.findChild(QPushButton, "V9ExportButton").click()
        app.processEvents()
        menu = captured.get("menu")
        assert menu is not None
        image_action = next(action for action in menu.actions() if action.data() == "image")
        image_action.trigger()
        window.image_browser_workspace._cached_image = None
        assert export_calls == [str(tmp_path / "native.png")]
        assert not window.is_project_dirty()

        window.switch_language("zh")
        app.processEvents()
        assert window.module_selector.button_for_module("viewer").toolTip() == "图片浏览"
        assert window.v9_shell._stage_buttons == []
        assert window.image_browser_workspace.export_button.text() == "按原分辨率导出 PNG"
    finally:
        window.switch_language("en")
        _close_widget(window, app)


def test_image_browser_sends_selected_candidates_to_fluorescence_without_switching(monkeypatch, tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    for path, color in ((first, QColor("#123456")), (second, QColor("#654321"))):
        image = QImage(64, 48, QImage.Format.Format_RGB32)
        image.fill(color)
        assert image.save(str(path))

    received: list[list[str]] = []

    def capture_import(window, paths):
        received.append([str(path) for path in paths])

    def capture_transfer(window, payloads):
        received.append([str(item.get("path") if isinstance(item, dict) else item) for item in payloads])

    monkeypatch.setattr(MainWindow, "import_fluorescence_images", capture_import)
    monkeypatch.setattr(MainWindow, "import_browser_fluorescence_transfer", capture_transfer)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.set_module("viewer")
        workspace = window.image_browser_workspace
        workspace.open_folder(tmp_path)
        workspace.thread_pool.waitForDone(2000)
        app.processEvents()
        workspace.thumbnail_list.setCurrentRow(0)
        workspace.thread_pool.waitForDone(2000)
        app.processEvents()
        workspace.um_per_px_spin.setValue(0.5)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.thumbnail_list.item(0).setSelected(True)
        workspace.thumbnail_list.item(1).setSelected(True)
        workspace.send_selected_to_fluorescence()
        workspace.thread_pool.waitForDone(2000)
        app.processEvents()
        workspace.thread_pool.waitForDone(2000)
        app.processEvents()

        assert received and [Path(path) for path in received[-1]] == [first, second]
        assert not any(Path(path).parent == workspace.transfer_dir for path in received[-1])
        assert window.active_module == "viewer"
        assert window.canvas_stack.currentWidget() is workspace.canvas
    finally:
        _close_widget(window, app)


def test_style_lab_workspace_exposes_only_current_design() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    workspace = StyleLabWorkspace()
    workspace.resize(1180, 720)
    workspace.show()
    app.processEvents()

    labels = [label.text() for label in workspace.findChildren(QLabel)]
    assert "Journal Preset Library" not in labels
    assert "Reference Style Preview" not in labels
    assert workspace.findChild(QComboBox, "StyleLabPresetCombo") is None
    assert workspace.local_extraction.btn_extract.isVisible()

    _close_widget(workspace, app)


def test_style_lab_main_window_qss_keeps_scroll_children_viewport_width(desktop_screen) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    previous_stylesheet = app.styleSheet()
    qss_path = Path(__file__).resolve().parents[1] / "resources" / "sciefig.qss"
    app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.resize(1280, 760)
        window.set_module("stylelab")
        window.show()
        app.processEvents()

        workspace = window.stylelab_workspace
        assert workspace.findChild(QScrollArea, "StyleLabLibraryScroll") is None
        editor = workspace.local_extraction
        viewport_width = workspace.width()
        for button in (editor.btn_open, editor.btn_extract, editor.btn_library, editor.btn_apply, editor.btn_saved):
            top_left = button.mapTo(workspace, QPoint(0, 0))
            right_edge = top_left.x() + button.width()
            assert button.width() > 36
            assert top_left.x() >= 0
            assert right_edge <= viewport_width + 1
    finally:
        _close_widget(window, app)
        app.setStyleSheet(previous_stylesheet)
        app.processEvents()


def test_style_sidebar_stylelab_mode_scrolls_and_avoids_fixed_width() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    sidebar = StyleSidebar()
    sidebar.resize(180, 360)
    sidebar.set_module("stylelab")
    sidebar.show()
    app.processEvents()

    scroll = sidebar.findChild(QScrollArea, "StyleSidebarScroll")
    assert scroll is not None
    assert sidebar.minimumWidth() < 220
    assert sidebar.maximumWidth() > 180
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff

    labels = [label.text() for label in scroll.widget().findChildren(QLabel)]
    assert "EXTRACT STYLE FROM REFERENCE" in labels
    assert not any("placeholders" in text.lower() for text in labels)
    assert all(button.toolTip() for button in scroll.widget().findChildren(QPushButton))

    _close_widget(sidebar, app)


def test_v9_shell_rail_collapses_restores_and_preserves_module_switching() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.show()
    app.processEvents()

    window.v9_shell.collapse_rail()
    app.processEvents()

    assert window.v9_shell.is_rail_collapsed()
    assert not window.module_selector.isVisible()
    assert window.v9_shell.rail_panel.isVisible()
    assert window.v9_shell.body_splitter.sizes()[0] == 0
    assert window.canvas_stack.isVisible()
    assert window.v9_shell.rail_reopen_button.isVisible()

    window.v9_shell.restore_rail()
    app.processEvents()
    window.module_selector.button_for_module("ihc").click()
    app.processEvents()

    assert not window.v9_shell.is_rail_collapsed()
    assert window.module_selector.isVisible()
    assert not window.v9_shell.rail_reopen_button.isVisible()
    assert window.active_module == "ihc"

    _close_widget(window, app)


def test_v9_shell_left_rail_resizes_and_restore_keeps_user_width(desktop_screen) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1366, 768)
    window.show()
    app.processEvents()

    window.v9_shell.set_rail_width(156)
    app.processEvents()

    assert window.v9_shell.rail_width() >= 150
    assert window.module_selector.width() >= 140

    window.v9_shell.collapse_rail()
    app.processEvents()
    assert window.v9_shell.rail_width() == 0
    assert window.v9_shell.rail_reopen_button.isVisible()

    window.v9_shell.restore_rail()
    app.processEvents()
    assert window.v9_shell.rail_width() >= 150
    assert not window.v9_shell.rail_reopen_button.isVisible()

    _close_widget(window, app)


def test_v9_shell_left_rail_labels_do_not_clip_when_resized() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1366, 768)
    window.show()
    app.processEvents()

    assert window.v9_shell.rail_width() >= 76
    for _module_id, button in window.module_selector._buttons.items():
        text = button.text()
        assert text
        assert button.fontMetrics().horizontalAdvance(text) <= button.width() - 2

    window.v9_shell.set_rail_width(80)
    app.processEvents()
    assert window.v9_shell.rail_width() >= 76
    for button in window.module_selector._buttons.values():
        text = button.text()
        assert text
        assert button.fontMetrics().horizontalAdvance(text) <= button.width() - 2

    _close_widget(window, app)


def test_v9_shell_right_panel_collapses_restores_and_resizes(desktop_screen) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1366, 768)
    window.show()
    app.processEvents()

    assert 360 <= window.v9_shell.right_panel_width() <= 362

    window.v9_shell.set_right_panel_width(360)
    app.processEvents()
    assert window.v9_shell.right_panel_width() >= 360

    window.v9_shell.collapse_right_panel()
    app.processEvents()

    assert window.v9_shell.is_right_panel_collapsed()
    assert window.v9_shell.right_panel.isVisible()
    assert window.v9_shell.body_splitter.sizes()[2] == 0
    assert window.canvas_stack.isVisible()
    assert window.v9_shell.right_show_button.isVisible()

    window.v9_shell.right_show_button.click()
    app.processEvents()

    assert not window.v9_shell.is_right_panel_collapsed()
    assert window.v9_shell.right_panel.isVisible()
    assert not window.v9_shell.right_show_button.isVisible()
    assert 360 <= window.v9_shell.right_panel_width() <= 400

    window.v9_shell.right_show_button.click()
    app.processEvents()
    assert not window.v9_shell.is_right_panel_collapsed()

    _close_widget(window, app)


def test_v9_shell_bottom_language_buttons_call_switch_language(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    calls: list[str] = []
    monkeypatch.setattr(window, "switch_language", calls.append)

    window.findChild(QPushButton, "V9LanguageButtonZh").click()
    window.findChild(QPushButton, "V9LanguageButtonEn").click()
    app.processEvents()

    assert calls == ["zh", "en"]

    _close_widget(window, app)


def test_v9_shell_language_switch_retranslates_visible_chrome() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1366, 768)
    window.show()
    try:
        window.set_module("fluoro")
        app.processEvents()

        window.switch_language("zh")
        app.processEvents()

        assert window.findChild(QPushButton, "V9NewButton").text() == "新建"
        assert window.findChild(QPushButton, "V9ExportButton").text() == "导出"
        assert window.module_selector.button_for_module("brightfield").text() == "明场"
        assert window.v9_shell.stage_title_label.text() == "荧光"
        assert window.v9_shell.right_panel_title.text() == "荧光工作流"
        assert window.v9_shell.workflow_row.isVisible()
        assert window.fluoro_workspace.mode_combo.itemText(0) == "辅助"
        assert window.fluoro_workspace.active_image_group.isHidden()
        window.fluoro_workspace.set_step(4)
        fluoro_labels = [label.text() for label in window.fluoro_workspace.workflow_panel.findChildren(QLabel)]
        assert "步骤 5/6：排布合并/拆分预览和比例尺。" == window.fluoro_workspace.step_hint.text()
        assert "排列方式" in fluoro_labels
        assert "统计图宽" in fluoro_labels
        assert window.fluoro_workspace.active_image_group.isHidden()

        window.set_module("qpcr")
        app.processEvents()
        qpcr_labels = [button.text().replace("✓ ", "").replace("• ", "").replace("○ ", "") for button in window.data_panel.qpcr_step_buttons]
        assert qpcr_labels == ["导入", "分析", "布局", "导出"]
        window.show_qpcr_layout()
        qpcr_group_titles = [group.title() for group in window.qpcr_layout_workspace.tools_panel.findChildren(QGroupBox)]
        assert "文字格式" in qpcr_group_titles
        assert "统计图" in qpcr_group_titles
        assert window.qpcr_layout_workspace.to_composer_button.text() == "添加 qPCR 图"

        window.set_module("wb")
        app.processEvents()
        wb_labels = [button.text().replace("✓ ", "").replace("● ", "").replace("○ ", "") for button in window.data_panel.wb_step_buttons]
        assert wb_labels == ["导入", "裁剪", "标签", "密度", "统计", "布局", "导出"]

        window.set_module("composer")
        app.processEvents()
        assert window.composer_workspace.assets_button.text() == "素材"
        assert window.composer_workspace.add_page_button.toolTip() == "添加页面"
        assert window.composer_workspace.tool_buttons['rectangle'].toolTip() == "矩形"

        window.switch_language("en")
        app.processEvents()

        assert window.findChild(QPushButton, "V9NewButton").text() == "New"
        assert window.module_selector.button_for_module("brightfield").text() == "Bright"
        assert window.v9_shell.stagebar.isHidden()
    finally:
        window.switch_language("en")
        _close_widget(window, app)


def test_v9_shell_toolbar_buttons_and_preset_control_are_present_and_wired(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    calls: list[str] = []
    monkeypatch.setattr(window, "new_project", lambda: calls.append("new"))
    monkeypatch.setattr(window, "open_project", lambda: calls.append("open"))
    monkeypatch.setattr(window, "save_project", lambda: calls.append("save"))
    monkeypatch.setattr(window, "save_project_as", lambda: calls.append("save_as"))
    monkeypatch.setattr(window, "export_active_module", lambda: calls.append("export"))

    for object_name in ("V9NewButton", "V9OpenButton", "V9SaveButton", "V9SaveAsButton", "V9ExportButton"):
        button = window.findChild(QPushButton, object_name)
        assert button is not None
        assert button.isVisible() or not window.isVisible()

    toolbar = window.findChild(QFrame, "V9GlobalToolbar")
    assert toolbar is not None
    assert toolbar.findChild(QPushButton, "V9RailToggleButton") is None
    assert toolbar.findChild(QPushButton, "V9RightToggleButton") is None
    assert window.findChild(QPushButton, "V9RailReopenButton") is not None

    window.findChild(QPushButton, "V9NewButton").click()
    window.findChild(QPushButton, "V9OpenButton").click()
    window.findChild(QPushButton, "V9SaveButton").click()
    window.findChild(QPushButton, "V9SaveAsButton").click()
    window.findChild(QPushButton, "V9ExportButton").click()

    preset = window.findChild(QComboBox, "V9StylePresetCombo")
    assert preset is not None
    assert preset.currentText() == "Nature"
    assert calls == ["new", "open", "save", "save_as", "export"]

    _close_widget(window, app)


def test_clean_new_open_close_do_not_prompt(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    original_project = window.project

    monkeypatch.setattr(main_window_module.QMessageBox, "question", lambda *args, **kwargs: pytest.fail("clean action prompted"))
    monkeypatch.setattr(main_window_module.QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("", ""))

    window.open_project()
    assert window.project is original_project
    window.new_project()

    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    window.deleteLater()
    app.processEvents()


def test_unsaved_question_fixture_delegates_other_titles(discard_unsaved_changes_questions, monkeypatch) -> None:
    delegated_calls = []

    def fake_original(*args, **kwargs):
        delegated_calls.append((args, kwargs))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(discard_unsaved_changes_questions, "original_question", fake_original)

    assert QMessageBox.question(None, "Unsaved Changes", "Save changes?") == QMessageBox.StandardButton.Discard
    assert delegated_calls == []
    assert QMessageBox.question(None, "Remove Panel", "Remove this panel?") == QMessageBox.StandardButton.No
    assert delegated_calls[0][0][1] == "Remove Panel"


@pytest.mark.parametrize(
    ("response", "save_path", "replaces_project"),
    [
        (QMessageBox.StandardButton.Cancel, None, False),
        (QMessageBox.StandardButton.Discard, None, True),
        (QMessageBox.StandardButton.Save, "", False),
    ],
)
def test_dirty_new_confirmation_preserves_or_replaces_project(monkeypatch, response, save_path, replaces_project) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    original_project = window.project
    original_root = Path(tempfile.mkdtemp(prefix="sciefig_ui_new_"))
    original_project.archive_extract_root = original_root
    original_baseline = window._clean_project_fingerprint
    window.project.title = "Edited before new"
    assert window.is_project_dirty()
    _patch_message_box_question(monkeypatch, response)

    if save_path is None:
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: pytest.fail("save dialog should not open"))
    else:
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (save_path, ""))

    window.new_project()

    if replaces_project:
        assert window.project is not original_project
        assert not window.is_project_dirty()
        assert not original_root.exists()
    else:
        assert window.project is original_project
        assert window._clean_project_fingerprint == original_baseline
        assert window.is_project_dirty()
        assert original_root.exists()
    _close_widget(window, app)


@pytest.mark.parametrize("response", [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save])
def test_dirty_open_cancel_or_save_cancel_preserves_project(monkeypatch, response) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    original_project = window.project
    original_root = Path(tempfile.mkdtemp(prefix="sciefig_ui_cancel_"))
    original_project.archive_extract_root = original_root
    original_baseline = window._clean_project_fingerprint
    window.project.title = "Edited before open"
    assert window.is_project_dirty()
    _patch_message_box_question(monkeypatch, response)

    if response == QMessageBox.StandardButton.Save:
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
    else:
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: pytest.fail("save dialog should not open"))
    monkeypatch.setattr(main_window_module.QFileDialog, "getOpenFileName", lambda *args, **kwargs: pytest.fail("open dialog should not open"))

    window.open_project()

    assert window.project is original_project
    assert original_root.exists()
    assert window._clean_project_fingerprint == original_baseline
    assert window.is_project_dirty()
    _close_widget(window, app)


def test_successful_open_replaces_project_and_resets_dirty_state(monkeypatch, tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    original_root = Path(tempfile.mkdtemp(prefix="sciefig_ui_replace_"))
    window.project.archive_extract_root = original_root
    window.project.title = "Edited before open"
    loaded_project = window._create_default_project()
    loaded_project.title = "Loaded project"
    _patch_message_box_question(monkeypatch, QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(main_window_module.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(tmp_path / "loaded.sfig"), ""))
    from ui.controllers import project_open_controller
    monkeypatch.setattr(project_open_controller.Project, "load_sfig", staticmethod(lambda path: loaded_project))
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:pytest.fail('Unexpected open-project warning'))

    window.open_project()

    assert window.project is loaded_project
    assert not window.is_project_dirty()
    assert not original_root.exists()
    _close_widget(window, app)


def test_project_fingerprint_covers_nested_panel_composer_and_audit_edits() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    assert not window.is_project_dirty()

    window.project.panels[0].data["nested_edit"] = {"value": 1}
    assert window.is_project_dirty()
    window._capture_clean_project_baseline()
    window.project.composer_layout["manual_edit"] = {"x": 2}
    assert window.is_project_dirty()
    window._capture_clean_project_baseline()
    window.project.append_audit("test_edit", "audit edit")
    assert window.is_project_dirty()
    _close_widget(window, app)


def test_save_project_success_resets_dirty_state_and_returns_true(monkeypatch, tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project.title = "Edited before save"
    saved_paths = []
    monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(tmp_path / "saved"), ""))
    monkeypatch.setattr(window.project, "save_sfig", lambda path: saved_paths.append(path))

    assert window.save_project() is True
    assert [Path(path) for path in saved_paths] == [tmp_path / "saved.sfig"]
    assert not window.is_project_dirty()
    _close_widget(window, app)


def test_failed_save_keeps_dirty_state_and_warns_without_exposing_path(monkeypatch, tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project.title = "Edited before failed save"
    warning_calls = []
    secret_path = str(tmp_path / "private" / "sensitive.sfig")

    def fail_save(path):
        raise OSError(secret_path)

    monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (secret_path, ""))
    monkeypatch.setattr(window.project, "save_sfig", fail_save)
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda *args, **kwargs: warning_calls.append((args, kwargs)))

    assert window.save_project() is False
    assert window.is_project_dirty()
    assert len(warning_calls) == 1
    assert secret_path not in str(warning_calls[0])
    _close_widget(window, app)


def test_failed_open_preserves_project_and_warns(monkeypatch, tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    original_project = window.project
    original_root = Path(tempfile.mkdtemp(prefix="sciefig_ui_failed_"))
    original_project.archive_extract_root = original_root
    original_baseline = window._clean_project_fingerprint
    warning_calls = []
    failed_path = str(tmp_path / "broken.sfig")

    def fail_load(path):
        raise OSError(failed_path)

    monkeypatch.setattr(main_window_module.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (failed_path, ""))
    monkeypatch.setattr(main_window_module.Project, "load_sfig", staticmethod(fail_load))
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda *args, **kwargs: warning_calls.append((args, kwargs)))

    window.open_project()

    assert window.project is original_project
    assert original_root.exists()
    assert window._clean_project_fingerprint == original_baseline
    assert not window.is_project_dirty()
    assert len(warning_calls) == 1
    assert failed_path not in str(warning_calls[0])
    _close_widget(window, app)


def test_dirty_close_cancel_ignores_event_without_cleaning_scenes(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project.title = "Edited before close"
    _patch_message_box_question(monkeypatch, QMessageBox.StandardButton.Cancel)
    cleanup_calls = []
    monkeypatch.setattr(main_window_module, "cleanup_registered_graphics_scenes", lambda: cleanup_calls.append(True))

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert cleanup_calls == []
    window.deleteLater()
    app.processEvents()


def test_dirty_close_discard_accepts_and_cleans_scenes(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    extract_root = Path(tempfile.mkdtemp(prefix="sciefig_ui_close_"))
    window.project.archive_extract_root = extract_root
    window.project.title = "Edited before close"
    _patch_message_box_question(monkeypatch, QMessageBox.StandardButton.Discard)
    cleanup_calls = []
    monkeypatch.setattr(main_window_module, "cleanup_registered_graphics_scenes", lambda: cleanup_calls.append(True))

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert cleanup_calls == [True]
    assert not extract_root.exists()
    window.deleteLater()
    app.processEvents()


def test_set_module_ihc_uses_dedicated_workspace_and_hides_top_data_panel() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("ihc")

    assert window.active_module == "ihc"
    assert window.data_panel.isHidden()
    assert window.canvas_stack.currentWidget() is window.ihc_workspace
    assert not window.workflow_ui.footers['ihc'].primary.isHidden()
    assert window.workflow_ui.footers['ihc'].primary.text() == window.ihc_workspace.import_button.text()

    _close_widget(window, app)


def test_set_module_ihc_shows_workflow_controls() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("ihc")
    window.show()
    app.processEvents()

    assert window.ihc_workspace.isVisible()
    assert window.data_panel.ihc_context_panel.isVisible()
    assert [button.text() for button in window.data_panel.ihc_step_buttons] == ["Import", "Measure", "Stats", "Layout"]
    assert window.data_panel.ihc_context_stack.currentIndex() == 0
    assert window.data_panel.ihc_active_image_combo is not None
    assert not window.data_panel.ihc_run_analysis_button.isVisible()

    _close_widget(window, app)


def test_import_ihc_images_sets_active_image_and_preview(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = tmp_path / "ihc-sample.png"
    image = QImage(96, 72, QImage.Format.Format_RGB32)
    image.fill(0xFFD8C9B6)
    assert image.save(str(image_path))

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])

    panel = window._active_ihc_panel()
    active_image = panel.active_image()
    assert active_image is not None
    assert active_image["source_name"] == image_path.name
    assert window.ihc_workspace.canvas.image_label is not None
    assert window.ihc_workspace.canvas._source_pixmap is not None

    _close_widget(window, app)


def test_ihc_workspace_import_button_uses_import_helper(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = tmp_path / "ihc-import-button.png"
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(0xFFD8C9B6)
    assert image.save(str(image_path))

    window.set_module("ihc")
    window.show()
    app.processEvents()

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getOpenFileNames",
        lambda *args, **kwargs: ([str(image_path)], ""),
    )

    window.ihc_workspace.import_button.click()
    app.processEvents()

    active_image = window._active_ihc_panel().active_image()
    assert active_image is not None
    assert active_image["source_name"] == image_path.name
    assert window.ihc_workspace.canvas._source_pixmap is not None
    assert window.ihc_workspace.canvas.image_label is not None

    _close_widget(window, app)


def test_ihc_workspace_drop_imports_supported_raster(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = tmp_path / "ihc-drop.png"
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(0xFFD8C9B6)
    assert image.save(str(image_path))

    window.set_module("ihc")
    event = _DropEvent(_mime_for(image_path))

    window.ihc_workspace.dropEvent(event)
    app.processEvents()

    active_image = window._active_ihc_panel().active_image()
    assert event.accepted
    assert active_image is not None
    assert active_image["source_name"] == image_path.name
    assert window.ihc_workspace.canvas._source_pixmap is not None

    _close_widget(window, app)


def test_run_ihc_analysis_populates_results_table(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.data_panel.ihc_positive_threshold_spin.setValue(0.30)
    window.data_panel.ihc_weak_threshold_spin.setValue(0.20)
    window.data_panel.ihc_moderate_threshold_spin.setValue(0.50)
    window.data_panel.ihc_strong_threshold_spin.setValue(0.80)
    window.data_panel.ihc_min_area_spin.setValue(2)

    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    table = window.data_panel.ihc_results_table
    assert table.rowCount() == 1
    assert table.item(0, 0) is not None
    assert table.item(0, 0).text() == image_path.name
    assert table.item(0, 1) is not None
    assert table.item(0, 1).text() == "Ungrouped"
    assert table.item(0, 2) is not None
    assert table.item(0, 2).text()
    assert table.item(0, 3) is not None
    assert table.item(0, 3).text()
    assert table.item(0, 4) is not None
    assert table.item(0, 4).text()
    assert "H-score" in window.data_panel.ihc_summary_label.text()

    _close_widget(window, app)


def test_run_ihc_analysis_uses_selected_roi_and_appends_measurements(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_roi_ui.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.data_panel.ihc_min_area_spin.setValue(2)

    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    first = window._active_ihc_panel().analysis["ihc_measurements"][-1]

    window.set_ihc_workflow_step(1)
    window.set_ihc_roi_from_canvas(QRect(0, 0, 2, 4))
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    second = window._active_ihc_panel().analysis["ihc_measurements"][-1]

    assert window.data_panel.ihc_results_table.rowCount() == 2
    assert len(window._active_ihc_panel().active_image()["measurements"]) == 2
    assert first["measurement_uuid"] != second["measurement_uuid"]
    assert first["results"]["positive_area_px"] == 9
    assert second["results"]["positive_area_px"] == 6
    assert second["roi"]["items"][-1]["coordinates_px"] == {"x": 0, "y": 0, "w": 2, "h": 4}
    assert window.data_panel.selected_ihc_measurement_uuid() == second["measurement_uuid"]
    assert window.ihc_workspace.current_roi() == QRect(0, 0, 2, 4)

    _close_widget(window, app)


def test_selecting_ihc_result_restores_measurement_roi(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_selection.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.data_panel.ihc_min_area_spin.setValue(2)
    window.set_ihc_workflow_step(1)

    window.set_ihc_roi_from_canvas(QRect(0, 0, 2, 4))
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    first = window._active_ihc_panel().analysis["ihc_measurements"][-1]

    window.set_ihc_workflow_step(1)
    window.set_ihc_roi_from_canvas(QRect(0, 0, 6, 6))
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    second = window._active_ihc_panel().analysis["ihc_measurements"][-1]

    table = window.data_panel.ihc_results_table
    table.selectRow(0)
    app.processEvents()
    assert window.data_panel.measurement_uuid_for_row(0) == first["measurement_uuid"]
    assert window.ihc_workspace.current_roi() == QRect(0, 0, 2, 4)
    assert window._active_ihc_panel().active_roi()["coordinates_px"] == {"x": 0, "y": 0, "w": 2, "h": 4}

    table.selectRow(1)
    app.processEvents()
    assert window.data_panel.measurement_uuid_for_row(1) == second["measurement_uuid"]
    assert window.ihc_workspace.current_roi() == QRect(0, 0, 6, 6)
    assert window._active_ihc_panel().active_roi()["coordinates_px"] == {"x": 0, "y": 0, "w": 6, "h": 6}

    _close_widget(window, app)


def test_copy_ihc_results_table_puts_tsv_on_clipboard(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_copy.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.data_panel.ihc_min_area_spin.setValue(2)
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")

    window.data_panel.ihc_copy_table_button.click()

    copied = QApplication.clipboard().text()
    assert "Image\tGroup\tPositive Area %" in copied
    assert image_path.name in copied
    assert "Ungrouped" in copied

    _close_widget(window, app)


def test_run_ihc_statistics_populates_results_and_persists_analysis(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_a = _write_synthetic_ihc_png(tmp_path / "control_ihc.png")
    image_b = _write_synthetic_ihc_png(tmp_path / "treated_ihc.png")

    window.set_module("ihc")
    window.data_panel.ihc_min_area_spin.setValue(2)
    window.import_ihc_images([str(image_a)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    window.import_ihc_images([str(image_b)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")

    assert window.data_panel.ihc_stats_group_table.rowCount() == 2
    window.data_panel.ihc_stats_group_table.item(0, 1).setText("Control")
    window.data_panel.ihc_stats_group_table.item(1, 1).setText("Treatment")
    window.data_panel.ihc_stats_reference_group_combo.setCurrentText("Control")

    window.run_ihc_statistics()

    stats_table = window.data_panel.ihc_stats_result_table
    assert stats_table.rowCount() == 2
    assert [stats_table.item(row, 0).text() for row in range(stats_table.rowCount())] == ["Control", "Treatment"]
    assert window._active_ihc_panel().analysis["ihc_statistics"] == window.data_panel.analysis["ihc_statistics"]
    assert window.data_panel.analysis["ihc_statistics"]["metric"] == "Positive Area %"
    assert len(window.data_panel.analysis["ihc_statistics"]["plot_data"]) == 2
    assert [row["image_name"] for row in window.data_panel.analysis["ihc_statistics"]["plot_data"]] == [
        image_a.name,
        image_b.name,
    ]
    assert window.data_panel.ihc_stats_summary_label.text()

    _close_widget(window, app)


def test_copy_and_export_ihc_statistics_table(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_a = _write_synthetic_ihc_png(tmp_path / "control_copy_stats.png")
    image_b = _write_synthetic_ihc_png(tmp_path / "treated_copy_stats.png")
    export_path = tmp_path / "ihc_statistics.csv"

    window.set_module("ihc")
    window.data_panel.ihc_min_area_spin.setValue(2)
    window.import_ihc_images([str(image_a)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    window.import_ihc_images([str(image_b)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    window.data_panel.ihc_stats_group_table.item(0, 1).setText("Control")
    window.data_panel.ihc_stats_group_table.item(1, 1).setText("Treatment")
    window.data_panel.ihc_stats_reference_group_combo.setCurrentText("Control")
    window.run_ihc_statistics()

    window.copy_ihc_statistics_table()

    copied = QApplication.clipboard().text()
    assert "Group\tn\tMean\tSEM" in copied
    assert "Control" in copied
    assert "Treatment" in copied

    window.export_ihc_statistics_csv_to_path(export_path)

    exported = export_path.read_text(encoding="utf-8")
    assert "Group,n,Mean,SEM" in exported
    assert "Control" in exported
    assert "Treatment" in exported

    _close_widget(window, app)


def test_selectable_image_label_maps_click_to_source_pixels() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    label = SelectableImageLabel()
    label.resize(20, 20)
    source = QPixmap(10, 10)
    source.fill(QColor("white"))
    display = source.scaled(20, 20)

    label.set_source_pixmap(source, display)

    mapped = label._map_label_point_to_source(QPoint(10, 10))

    assert mapped == QPoint(5, 5)
    app.processEvents()


def test_assisted_ihc_example_clicks_queue_examples_then_recalibrate_and_revert(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_assisted.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.data_panel.set_ihc_guided_mode("assisted")
    window.set_ihc_workflow_step(1)
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")

    panel = window._active_ihc_panel()
    active_image_uid = str(panel.active_image()["uid"])
    auto_measurement = panel.analysis["ihc_measurements"][-1]
    auto_measurement_count = len(panel.analysis["ihc_measurements"])

    window.data_panel.ihc_mark_positive_button.setChecked(True)
    app.processEvents()
    window.ihc_workspace.point_selected.emit(QPoint(1, 1))
    app.processEvents()

    assert len(panel.analysis["ihc_measurements"]) == auto_measurement_count
    assert panel.analysis["ihc_measurements"][-1]["measurement_uuid"] == auto_measurement["measurement_uuid"]
    assert window.data_panel.current_ihc_example_click_mode() == "positive"
    assert window.ihc_workspace.canvas.image_label.point_selection_enabled
    assert window.ihc_workspace.canvas.example_marker_count() == 1
    assert len(window._ihc_example_samples_by_image[active_image_uid]) == 1
    assert window.data_panel.ihc_examples_list.count() == 1

    window.data_panel.ihc_mark_negative_button.setChecked(True)
    app.processEvents()
    window.ihc_workspace.point_selected.emit(QPoint(4, 4))
    app.processEvents()

    assert len(panel.analysis["ihc_measurements"]) == auto_measurement_count
    assert panel.analysis["ihc_measurements"][-1]["measurement_uuid"] == auto_measurement["measurement_uuid"]
    assert len(window._ihc_example_samples_by_image[active_image_uid]) == 2
    assert window.data_panel.ihc_examples_list.count() == 2
    assert window.ihc_workspace.canvas.example_marker_count() == 2

    window.data_panel.ihc_examples_list.setCurrentRow(1)
    window.delete_selected_ihc_example()
    app.processEvents()

    assert len(panel.analysis["ihc_measurements"]) == auto_measurement_count
    assert len(window._ihc_example_samples_by_image[active_image_uid]) == 1
    assert window.data_panel.ihc_examples_list.count() == 1
    assert window.ihc_workspace.canvas.example_marker_count() == 1

    window.data_panel.ihc_recalibrate_button.click()
    app.processEvents()

    recalibrated = panel.analysis["ihc_measurements"][-1]
    assert recalibrated["threshold_source"] == "example_guided"
    assert recalibrated["example_guided_result"]["positive_sample_count"] == 1
    assert recalibrated["example_guided_result"]["negative_sample_count"] == 0

    window.revert_ihc_to_auto()

    assert panel.data["guided_state"]["last_active_measurement_uuid"] == auto_measurement["measurement_uuid"]
    assert active_image_uid not in window._ihc_example_samples_by_image
    assert window.data_panel.selected_ihc_measurement_uuid() == auto_measurement["measurement_uuid"]
    assert window.ihc_workspace.canvas.example_marker_count() == 0

    _close_widget(window, app)


def test_foolproof_ihc_auto_analyze_uses_fixed_settings_logs_mode_switch_and_updates_banner_from_measurement(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_foolproof.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    panel = window._active_ihc_panel()
    captured: dict[str, object] = {}
    banner_capture: dict[str, object] = {}
    original_auto_analyze = type(panel).auto_analyze_dab

    def capture_auto_analyze(panel, *args, **kwargs):
        captured["settings"] = dict(kwargs.get("settings") or {})
        captured["guided_mode"] = kwargs.get("guided_mode")
        return original_auto_analyze(panel, *args, **kwargs)

    def capture_banner(status, warnings, metrics=None):
        banner_capture["status"] = status
        banner_capture["warnings"] = list(warnings)
        banner_capture["metrics"] = list(metrics or [])

    monkeypatch.setattr(type(panel), "auto_analyze_dab", capture_auto_analyze)
    monkeypatch.setattr(window.data_panel.ihc_reliability_banner, "set_status", capture_banner)

    window.data_panel.set_ihc_guided_mode("assisted")
    app.processEvents()
    window.data_panel.ihc_sensitivity_combo.setCurrentIndex(0)
    window.data_panel.ihc_cleanup_combo.setCurrentIndex(0)
    window.data_panel.ihc_tissue_correction_checkbox.setChecked(False)

    window.data_panel.set_ihc_guided_mode("foolproof")
    app.processEvents()
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")

    assert captured["guided_mode"] == "foolproof"
    assert captured["settings"] == {
        "sensitivity": "medium",
        "cleanup_level": "medium",
        "tissue_background_correction": True,
    }
    measurement = panel.analysis["ihc_measurements"][-1]
    assert banner_capture == {
        "status": measurement["reliability_status"],
        "warnings": list(measurement["warnings"]),
        "metrics": [
            ("Positive Area", f"{measurement['results']['positive_area_percent']:.2f}%"),
            ("H-score", f"{measurement['results']['h_score']:.1f}"),
        ],
    }
    assert not hasattr(main_window_module, "evaluate_ihc_reliability")
    assert [entry.detail for entry in panel.audit_log if entry.action == "ihc_mode_switch"][-2:] == [
        "old_mode=foolproof; new_mode=assisted; source=ihc_mode_combo",
        "old_mode=assisted; new_mode=foolproof; source=ihc_mode_combo",
    ]
    assert any(entry.action == "foolproof_auto_analyze" for entry in panel.audit_log)

    _close_widget(window, app)


def test_brightfield_foolproof_he_calls_auto_analyze_he(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield_foolproof_he.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.brightfield_stain_type_combo.setCurrentText("H&E")
    window.update_brightfield_image_metadata()
    panel = window._active_brightfield_panel()
    captured: dict[str, object] = {}
    original_auto_analyze = type(panel).auto_analyze_he

    def capture_auto_analyze(panel, *args, **kwargs):
        captured["settings"] = kwargs.get("settings")
        captured["guided_mode"] = kwargs.get("guided_mode")
        return original_auto_analyze(panel, *args, **kwargs)

    monkeypatch.setattr(type(panel), "auto_analyze_he", capture_auto_analyze)

    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert captured == {
        "settings": {"threshold_bias": 0},
        "guided_mode": "foolproof",
    }
    _close_widget(window, app)


def test_brightfield_assisted_he_calls_auto_analyze_he_with_guided_settings(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield_assisted_he.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.brightfield_stain_type_combo.setCurrentText("H&E")
    window.update_brightfield_image_metadata()
    window.data_panel.set_brightfield_guided_mode("assisted")
    window.data_panel.brightfield_sensitivity_combo.setCurrentIndex(0)
    window.data_panel.brightfield_cleanup_combo.setCurrentIndex(3)
    panel = window._active_brightfield_panel()
    captured: dict[str, object] = {}
    original_auto_analyze = type(panel).auto_analyze_he

    def capture_auto_analyze(panel, *args, **kwargs):
        captured["settings"] = dict(kwargs.get("settings") or {})
        captured["guided_mode"] = kwargs.get("guided_mode")
        return original_auto_analyze(panel, *args, **kwargs)

    monkeypatch.setattr(type(panel), "auto_analyze_he", capture_auto_analyze)

    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert captured == {
        "settings": {
            "sensitivity": "low",
            "cleanup_level": "high",
            "threshold_bias": 0,
            "small_region_filter_bias": 0,
        },
        "guided_mode": "assisted",
    }
    _close_widget(window, app)


def test_brightfield_expert_calls_create_measurement(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield_expert.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("expert")
    panel = window._active_brightfield_panel()
    captured: dict[str, object] = {}
    original_create_measurement = type(panel).create_measurement

    def capture_create_measurement(panel, *args, **kwargs):
        captured["settings"] = dict(kwargs.get("settings") or {})
        return original_create_measurement(panel, *args, **kwargs)

    monkeypatch.setattr(type(panel), "create_measurement", capture_create_measurement)

    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert captured["settings"]["segmentation_mode"] == window.data_panel.brightfield_analysis_settings()["segmentation_mode"]
    _close_widget(window, app)


def test_brightfield_foolproof_non_he_uses_profile_preset_measurement(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield_foolproof_non_he.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("foolproof")
    window.data_panel.brightfield_stain_type_combo.setCurrentText("Oil Red O")
    window.update_brightfield_image_metadata()
    info_calls: list[tuple[str, str]] = []

    def capture_information(_parent, title, message):
        info_calls.append((title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(main_window_module.QMessageBox, "information", capture_information)

    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    measurement = window._active_brightfield_panel().analysis["brightfield_measurements"][-1]
    assert measurement["threshold_source"] == "profile_preset"
    assert measurement["task_profile"]["task_id"] == "brightfield_oil_red_o_area"
    assert measurement["reliability_status"] == "WARN"
    assert info_calls == []
    _close_widget(window, app)


def test_brightfield_foolproof_visible_stain_combo_routes_current_run_when_metadata_mismatches(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield_combo_mismatch.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("foolproof")
    panel = window._active_brightfield_panel()
    assert panel.active_image()["stain_type"] == "H&E"

    window.data_panel.brightfield_stain_type_combo.setCurrentText("Oil Red O")
    assert window.data_panel.brightfield_stain_type_combo.currentText() == "Oil Red O"
    assert panel.active_image()["stain_type"] == "H&E"

    auto_calls: list[dict[str, object]] = []
    profile_calls: list[dict[str, object]] = []
    original_profile_measurement = type(panel).create_profile_preset_measurement

    def capture_auto_analyze(panel, *args, **kwargs):
        auto_calls.append(dict(kwargs))
        return panel.analysis["brightfield_measurements"][-1] if panel.analysis["brightfield_measurements"] else {}

    def capture_profile_measurement(panel, *args, **kwargs):
        profile_calls.append(dict(kwargs))
        return original_profile_measurement(panel, *args, **kwargs)

    monkeypatch.setattr(type(panel), "auto_analyze_he", capture_auto_analyze)
    monkeypatch.setattr(type(panel), "create_profile_preset_measurement", capture_profile_measurement)

    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert auto_calls == []
    assert profile_calls == [{"stain_type": "Oil Red O", "guided_mode": "foolproof"}]
    measurement = window._active_brightfield_panel().analysis["brightfield_measurements"][-1]
    assert measurement["task_profile"]["task_id"] == "brightfield_oil_red_o_area"
    _close_widget(window, app)


def test_brightfield_assisted_non_he_blocks_without_analysis(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield_assisted_block_non_he.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("assisted")
    window.data_panel.brightfield_stain_type_combo.setCurrentText("Oil Red O")
    window.update_brightfield_image_metadata()
    panel = window._active_brightfield_panel()
    profile_calls: list[dict[str, object]] = []
    manual_calls: list[dict[str, object]] = []
    info_calls: list[tuple[str, str]] = []

    def capture_profile_measurement(*args, **kwargs):
        profile_calls.append(dict(kwargs))
        return panel.analysis["brightfield_measurements"][-1] if panel.analysis["brightfield_measurements"] else {}

    def capture_create_measurement(*args, **kwargs):
        manual_calls.append(dict(kwargs))
        return panel.analysis["brightfield_measurements"][-1] if panel.analysis["brightfield_measurements"] else {}

    def capture_information(_parent, title, message):
        info_calls.append((title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(panel, "create_profile_preset_measurement", capture_profile_measurement)
    monkeypatch.setattr(panel, "create_measurement", capture_create_measurement)
    monkeypatch.setattr(main_window_module.QMessageBox, "information", capture_information)

    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert profile_calls == []
    assert manual_calls == []
    assert info_calls == [
        (
            "Brightfield",
            "Assisted Brightfield auto-analysis for non-H&E stains is not available in this phase.\n"
            "Use Foolproof for the experimental preset profile or switch to Expert for manual analysis.",
        )
    ]
    _close_widget(window, app)


def test_brightfield_mode_switch_writes_structured_audit(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield_mode_switch.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()

    window.data_panel.set_brightfield_guided_mode("assisted")
    app.processEvents()
    window.data_panel.set_brightfield_guided_mode("expert")
    app.processEvents()

    assert [entry.detail for entry in panel.audit_log if entry.action == "brightfield_mode_switch"][-2:] == [
        "old_mode=foolproof; new_mode=assisted; source=brightfield_mode_combo",
        "old_mode=assisted; new_mode=expert; source=brightfield_mode_combo",
    ]
    _close_widget(window, app)


def test_brightfield_batch_threshold_controls_lock_unlock_current_measurement(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    first_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_batch_first.png")
    second_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_batch_second.png")

    second_image = QImage(str(second_path))
    second_image.setPixelColor(4, 4, QColor(220, 145, 170))
    assert second_image.save(str(second_path))

    window.set_module("brightfield")
    window.import_brightfield_images([str(first_path), str(second_path)])
    panel = window._active_brightfield_panel()
    first_uid, second_uid = [image["uid"] for image in panel.images()]

    window.select_brightfield_image(first_uid)
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    first_measurement = panel.analysis["brightfield_measurements"][-1]

    assert not hasattr(window.data_panel, "brightfield_apply_batch_threshold_button")
    assert window.data_panel.brightfield_lock_batch_threshold_button.isEnabled()

    window.data_panel.brightfield_lock_batch_threshold_button.click()
    app.processEvents()
    assert panel.data["guided_state"]["batch_threshold_state"]["locked"] is True
    assert "Batch threshold: locked" in window.data_panel.brightfield_batch_threshold_status_label.text()

    window.select_brightfield_image(second_uid)
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    second_measurement = panel.analysis["brightfield_measurements"][-1]

    assert second_measurement["threshold_source"] == "batch_locked"
    assert second_measurement["params"]["a_min"] == pytest.approx(first_measurement["params"]["a_min"])
    assert any(entry.action == "brightfield_batch_threshold_apply" for entry in panel.audit_log)

    window.data_panel.brightfield_unlock_batch_threshold_button.click()
    app.processEvents()
    assert panel.data["guided_state"]["batch_threshold_state"]["locked"] is False
    status_text_after_unlock = window.data_panel.brightfield_batch_threshold_status_label.text()
    assert "waiting for Lock" in status_text_after_unlock
    assert "Batch threshold: locked" not in status_text_after_unlock
    assert window.data_panel.brightfield_unlock_batch_threshold_button.isEnabled() is False

    _close_widget(window, app)


def test_brightfield_batch_threshold_controls_disable_incompatible_measurement(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield_batch_incompatible.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("expert")
    window.data_panel.brightfield_stain_type_combo.setCurrentText("Oil Red O")
    window.update_brightfield_image_metadata()
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert not hasattr(window.data_panel, "brightfield_apply_batch_threshold_button")
    assert not window.data_panel.brightfield_lock_batch_threshold_button.isEnabled()
    assert "H&E LAB-mode measurement" in window.data_panel.brightfield_batch_threshold_status_label.text()

    _close_widget(window, app)


def test_brightfield_foolproof_sensitivity_change_starts_debounced_preview_and_preview_stays_transient(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_foolproof_debounce.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("foolproof")
    calls: list[str] = []
    monkeypatch.setattr(window, "preview_brightfield_segmentation", lambda: calls.append("preview"))

    window.data_panel.set_brightfield_foolproof_threshold_bias(2)

    assert window._brightfield_preview_debounce_timer.isActive()
    assert "300 ms" in window.data_panel.brightfield_preview_status_label.text()

    window._flush_brightfield_preview_debounce()

    assert calls == ["preview"]
    assert window._active_brightfield_panel().analysis["brightfield_measurements"] == []

    _close_widget(window, app)


def test_brightfield_assisted_sensitivity_change_starts_debounced_preview_and_preview_stays_transient(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_assisted_debounce.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("assisted")
    calls: list[str] = []
    monkeypatch.setattr(window, "preview_brightfield_segmentation", lambda: calls.append("preview"))

    window.data_panel.set_brightfield_assisted_threshold_bias(2)

    assert window._brightfield_preview_debounce_timer.isActive()
    assert "300 ms" in window.data_panel.brightfield_preview_status_label.text()

    window._flush_brightfield_preview_debounce()

    assert calls == ["preview"]
    assert window._active_brightfield_panel().analysis["brightfield_measurements"] == []

    _close_widget(window, app)


def test_brightfield_assisted_small_region_filter_change_starts_debounced_preview_and_preview_stays_transient(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_assisted_filter_debounce.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("assisted")
    calls: list[str] = []
    monkeypatch.setattr(window, "preview_brightfield_segmentation", lambda: calls.append("preview"))

    window.data_panel.set_brightfield_assisted_small_region_filter_bias(2)

    assert window._brightfield_preview_debounce_timer.isActive()
    assert "300 ms" in window.data_panel.brightfield_preview_status_label.text()

    window._flush_brightfield_preview_debounce()

    assert calls == ["preview"]
    assert window._active_brightfield_panel().analysis["brightfield_measurements"] == []

    _close_widget(window, app)


def test_brightfield_pending_guided_preview_is_canceled_when_final_analysis_runs(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_debounce_cancel_on_analyze.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.data_panel.set_brightfield_guided_mode("foolproof")
    calls: list[str] = []
    monkeypatch.setattr(window, "preview_brightfield_segmentation", lambda: calls.append("preview"))

    window.data_panel.set_brightfield_foolproof_threshold_bias(2)
    assert window._brightfield_preview_debounce_timer.isActive()

    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert window._brightfield_preview_debounce_timer.isActive() is False

    window._flush_brightfield_preview_debounce()

    assert calls == []
    assert "analysis complete" in window.statusBar().currentMessage().lower()

    _close_widget(window, app)


def test_brightfield_pending_guided_preview_is_canceled_when_image_switches(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    first_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_debounce_first.png")
    second_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_debounce_second.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(first_path), str(second_path)])
    panel = window._active_brightfield_panel()
    first_uid, second_uid = [image["uid"] for image in panel.images()]
    window.select_brightfield_image(first_uid)
    window.data_panel.set_brightfield_guided_mode("assisted")
    calls: list[str] = []
    monkeypatch.setattr(window, "preview_brightfield_segmentation", lambda: calls.append("preview"))

    window.data_panel.set_brightfield_assisted_threshold_bias(2)
    assert window._brightfield_preview_debounce_timer.isActive()

    window.select_brightfield_image(second_uid)

    assert window._brightfield_preview_debounce_timer.isActive() is False

    window._flush_brightfield_preview_debounce()

    assert calls == []
    assert window._active_brightfield_panel().active_image()["uid"] == second_uid

    _close_widget(window, app)


def test_brightfield_batch_threshold_lock_disables_foolproof_sensitivity_control(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    first_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_bias_lock_first.png")
    second_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_bias_lock_second.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(first_path), str(second_path)])
    panel = window._active_brightfield_panel()
    first_uid, _second_uid = [image["uid"] for image in panel.images()]

    window.select_brightfield_image(first_uid)
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    window.data_panel.brightfield_lock_batch_threshold_button.click()
    app.processEvents()

    assert window.data_panel.brightfield_foolproof_sensitivity_combo.isEnabled() is False

    window.data_panel.brightfield_unlock_batch_threshold_button.click()
    app.processEvents()

    assert window.data_panel.brightfield_foolproof_sensitivity_combo.isEnabled() is True

    _close_widget(window, app)


def test_brightfield_batch_threshold_lock_disables_assisted_sensitivity_control(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    first_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_assisted_bias_lock_first.png")
    second_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_assisted_bias_lock_second.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(first_path), str(second_path)])
    panel = window._active_brightfield_panel()
    first_uid, _second_uid = [image["uid"] for image in panel.images()]

    window.select_brightfield_image(first_uid)
    window.data_panel.set_brightfield_guided_mode("assisted")
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    window.data_panel.brightfield_lock_batch_threshold_button.click()
    app.processEvents()

    assert window.data_panel.brightfield_assisted_sensitivity_combo.isEnabled() is False
    assert window.data_panel.brightfield_assisted_small_region_filter_combo.isEnabled() is True

    window.data_panel.brightfield_unlock_batch_threshold_button.click()
    app.processEvents()

    assert window.data_panel.brightfield_assisted_sensitivity_combo.isEnabled() is True
    assert window.data_panel.brightfield_assisted_small_region_filter_combo.isEnabled() is True

    _close_widget(window, app)


def test_export_ihc_overlay_results_csv_and_summary_json_to_path(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_export.png")
    overlay_path = tmp_path / "ihc_overlay.png"
    results_path = tmp_path / "ihc_results.csv"
    summary_path = tmp_path / "ihc_summary.json"

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    measurement = window._active_ihc_panel().analysis["ihc_measurements"][-1]
    measurement["threshold_source"] = "example_guided"
    measurement["qc_messages"] = ["Weak stain contrast detected.", "Overlay looks sparse."]
    measurement["overlay_generated"] = True
    measurement["example_guided_result"] = {
        "positive_sample_count": 1,
        "negative_sample_count": 1,
        "threshold_shift_fraction": 0.25,
    }
    measurement["reliability"] = {
        "status": "WARN",
        "include_in_statistics": True,
        "messages": ["Review threshold shift before batch comparison."],
    }
    measurement["reliability_status"] = "WARN"
    measurement["warnings"] = [
        "Weak stain contrast detected.",
        "Review threshold shift before batch comparison.",
    ]

    window.export_ihc_overlay_png_to_path(overlay_path)
    window.export_ihc_results_csv_to_path(results_path)
    window.export_ihc_summary_json_to_path(summary_path)

    assert overlay_path.exists()
    assert results_path.exists()
    assert summary_path.exists()
    exported = results_path.read_text(encoding="utf-8")
    assert "Measurement UUID" in exported
    assert "Positive Threshold" in exported
    assert "Reliability Messages" in exported
    assert "QC Messages" in exported
    assert "Warnings" in exported
    assert "Example Guided Metadata" in exported
    assert "Review threshold shift before batch comparison." in exported
    assert "Weak stain contrast detected. | Overlay looks sparse." in exported
    assert image_path.name in exported

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["panel_id"] == window._active_ihc_panel().panel_id
    assert summary["measurements"][0]["reliability_status"] == "WARN"
    assert summary["measurements"][0]["threshold_source"] == "example_guided"
    assert summary["measurements"][0]["qc_messages"] == ["Weak stain contrast detected.", "Overlay looks sparse."]
    assert summary["measurements"][0]["warnings"] == [
        "Weak stain contrast detected.",
        "Review threshold shift before batch comparison.",
    ]
    assert summary["measurements"][0]["example_guided_result"]["positive_sample_count"] == 1

    _close_widget(window, app)


def test_run_ihc_analysis_blocks_coming_soon_task(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_coming_soon.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    panel = window._active_ihc_panel()
    called = {"count": 0}
    messages: list[str] = []

    monkeypatch.setattr(
        panel,
        "auto_analyze_dab",
        lambda *args, **kwargs: called.update(count=called["count"] + 1),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: messages.append(message),
    )

    window.data_panel.ihc_task_type_combo.setCurrentIndex(1)
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")

    assert called["count"] == 0
    assert messages == [
        "Task 'hematoxylin_rich' is not implemented in this release. "
        "Switch to 'DAB IHC / H-score' to run analysis."
    ]

    _close_widget(window, app)


def test_export_ihc_overlay_tiff_to_path_when_supported(tmp_path) -> None:
    supported = {bytes(fmt).decode("ascii", errors="ignore").lower() for fmt in QImageWriter.supportedImageFormats()}
    if "tif" not in supported and "tiff" not in supported:
        pytest.skip("Qt image plugins do not support TIFF in this environment.")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_export_tiff.png")
    overlay_path = tmp_path / "ihc_overlay.tiff"

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")

    exported_path = window.export_ihc_overlay_image_to_path(overlay_path)

    assert exported_path == overlay_path
    assert overlay_path.exists()
    assert not QImage(str(overlay_path)).isNull()

    _close_widget(window, app)


def test_ihc_stats_controls_default_fail_measurements_to_excluded(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_stats_gate.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")

    measurement = window._active_ihc_panel().analysis["ihc_measurements"][-1]
    measurement["reliability"] = {"status": "FAIL", "include_in_statistics": False}
    window.data_panel.populate_ihc_stats_controls(list(window._active_ihc_panel().analysis.get("ihc_measurements", [])))

    include_widget = window.data_panel.ihc_stats_group_table.cellWidget(0, 2)

    assert include_widget is not None
    assert include_widget.isChecked() is False

    _close_widget(window, app)


def test_ihc_export_and_composer_block_fail_reliability_measurements(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc_fail_gate.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")

    measurement = window._active_ihc_panel().analysis["ihc_measurements"][-1]
    measurement["overlay_generated"] = False
    measurement["qc_messages"] = ["Overlay generation failed or has not been confirmed."]
    measurement["reliability"] = {
        "status": "FAIL",
        "include_in_statistics": False,
        "messages": ["Overlay generation failed or has not been confirmed."],
    }
    measurement["reliability_status"] = "FAIL"

    with pytest.raises(ValueError, match="failed reliability review"):
        window.export_ihc_overlay_image_to_path(tmp_path / "blocked_overlay.png")

    messages: list[str] = []

    def _fake_information(parent, title, text):
        messages.append(f"{title}: {text}")
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", _fake_information)

    baseline_panel_count = len(window.project.panels)
    created = window.add_ihc_outputs_to_composer()

    assert created == []
    assert len(window.project.panels) == baseline_panel_count
    assert messages
    assert "failed reliability review" in messages[0]

    _close_widget(window, app)


def test_add_ihc_outputs_to_composer_creates_external_asset_panels(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    generated_asset_dir = tmp_path / "generated_assets"
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(generated_asset_dir))
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_a = _write_synthetic_ihc_png(tmp_path / "control_composer.png")
    image_b = _write_synthetic_ihc_png(tmp_path / "treated_composer.png")

    window.set_module("ihc")
    window.data_panel.ihc_min_area_spin.setValue(2)
    window.import_ihc_images([str(image_a)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    window.import_ihc_images([str(image_b)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    overlay_measurement_uuid = window._active_ihc_panel().analysis["ihc_measurements"][-1]["measurement_uuid"]
    overlay_measurement = window._active_ihc_panel().analysis["ihc_measurements"][-1]
    overlay_measurement["warnings"] = ["Confirm threshold shift before export."]
    overlay_measurement["qc_messages"] = ["Overlay looks sparse."]
    overlay_measurement["reliability"] = {
        "status": "WARN",
        "include_in_statistics": True,
        "messages": ["Confirm threshold shift before export."],
    }
    overlay_measurement["reliability_status"] = "WARN"
    window.data_panel.ihc_stats_group_table.item(0, 1).setText("Control")
    window.data_panel.ihc_stats_group_table.item(1, 1).setText("Treatment")
    window.data_panel.ihc_stats_reference_group_combo.setCurrentText("Control")
    window.run_ihc_statistics()

    baseline_panel_count = len(window.project.panels)
    baseline_shelf_count = window.composer_workspace.assets.count()

    created = window.add_ihc_outputs_to_composer()

    assert len(created) == 2
    assert len(window.project.panels) == baseline_panel_count + 2
    assert window.composer_workspace.assets.count() >= baseline_shelf_count + 2

    overlay_panel = next(
        panel for panel in created if isinstance(panel, ExternalImagePanel) and panel.data.get("source_module") == "ihc_overlay"
    )
    assert overlay_panel.data["source_panel_uuid"] == window._active_ihc_panel().panel_uuid
    assert overlay_panel.data["measurement_uuid"] == overlay_measurement_uuid
    assert overlay_panel.data["plot_type"] == "measurement_overlay"
    assert overlay_panel.data["source_dpi"] == 1200
    assert overlay_panel.data["reliability_status"] == "WARN"
    assert overlay_panel.data["warnings"] == ["Confirm threshold shift before export."]
    assert overlay_panel.data["qc_messages"] == ["Overlay looks sparse."]
    assert Path(overlay_panel.data["image_path"]).exists()

    stats_panel = next(
        panel for panel in created if isinstance(panel, ExternalImagePanel) and panel.data.get("source_module") == "ihc_statistics"
    )
    assert stats_panel.data["source_panel_uuid"] == window._active_ihc_panel().panel_uuid
    assert stats_panel.data["metric"] == "Positive Area %"
    assert stats_panel.data["plot_type"] == "statistics_chart"
    assert stats_panel.data["source_dpi"] == 1200
    assert Path(stats_panel.data["image_path"]).exists()
    assert stats_panel.data["source_format"] == "SVG"
    assert Path(stats_panel.data["image_path"]).suffix.lower() == ".svg"

    _close_widget(window, app)


def test_main_window_stays_inside_available_screen_on_show_and_module_switch() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    available = QRect(100, 80, 2200, 1800)
    window = _ScreenBoundMainWindow(Path(__file__).resolve().parents[1] / "resources", available)

    window.setGeometry(500, 300, 900, 650)
    window.show()
    app.processEvents()
    shown_geometry = window.geometry()
    assert shown_geometry.bottom() <= available.bottom()
    assert shown_geometry.right() <= available.right()

    window.setGeometry(50, 30, 900, 650)
    window.set_module("composer")
    app.processEvents()
    switched_geometry = window.geometry()
    assert switched_geometry.top() >= available.top()
    assert switched_geometry.left() >= available.left()

    _close_widget(window, app)


def test_qpcr_workflow_panel_uses_right_sidebar_layout_policies() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    data_panel = window.data_panel
    workflow_panel = window.qpcr_workflow_panel
    assert workflow_panel.parentWidget() is window.v9_shell.right_panel
    assert workflow_panel.objectName() == "QPCRWorkflowRightPanel"
    assert workflow_panel.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
    assert data_panel.parentWidget() is None
    assert data_panel.preview_table.minimumHeight() >= 320
    assert data_panel.preview_table.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert data_panel.preview_table.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
    assert data_panel.wb_band_table.sizePolicy().verticalPolicy() != QSizePolicy.Policy.Expanding
    assert data_panel.wb_strip_table.sizePolicy().verticalPolicy() != QSizePolicy.Policy.Expanding
    assert window.preview_canvas.paper.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Expanding
    assert window.preview_canvas.paper.sizePolicy().verticalPolicy() != QSizePolicy.Policy.Expanding

    _close_widget(window, app)


def test_module_workflow_panels_detach_without_center_layout_gaps() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    workspaces = [
        (FluorescenceWorkspace(), "workflow_panel", "canvas"),
        (IHCWorkspace(QWidget()), "workflow_panel", "canvas_stack"),
        (BrightfieldWorkspace(QWidget()), "workflow_panel", "canvas_stack"),
    ]

    for workspace, workflow_attr, center_attr in workspaces:
        workflow_panel = getattr(workspace, workflow_attr)
        center_widget = getattr(workspace, center_attr)
        host = QWidget()
        host_layout = QVBoxLayout(host)

        host_layout.addWidget(workflow_panel)
        workspace.resize(960, 640)
        host.resize(520, 640)
        workspace.show()
        host.show()
        app.processEvents()

        assert workspace.layout().count() == 1
        assert workspace.layout().itemAt(0).widget() is center_widget
        assert workflow_panel.parentWidget() is host
        assert workflow_panel.findChildren(QFrame, "V9RightWorkbenchGroup")
        assert center_widget.isVisibleTo(workspace)
        assert center_widget.width() >= 900

        _close_widget(workspace, app)
        _close_widget(host, app)


def test_ihc_workspace_keeps_canvas_large_and_moves_controls_off_top_row() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1366, 768)
    window.set_module("ihc")
    window.show()
    app.processEvents()

    assert window.data_panel.isHidden()
    assert window.canvas_stack.currentWidget() is window.ihc_workspace
    assert window.ihc_workspace.layout().count() == 1
    assert window.ihc_workspace.layout().itemAt(0).widget() is window.ihc_workspace.canvas_stack
    assert window.ihc_workspace.workflow_panel.parentWidget() is window.v9_shell.right_panel
    assert window.ihc_workspace.side_panel is window.ihc_workspace.workflow_panel
    assert window.ihc_workspace.active_image_group.isHidden() is False
    assert window.data_panel.ihc_workflow_group.isHidden() is True
    assert window.ihc_workspace.canvas.height() > 500
    assert window.ihc_workspace.height() > 620

    window.set_ihc_workflow_step(1)
    assert window.ihc_workspace.active_image_group.isHidden() is False
    assert window.data_panel.ihc_workflow_group.isHidden() is False

    _close_widget(window, app)


def test_ihc_module_switch_restores_layout_canvas_when_layout_step_is_active(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "ihc_layout_restore.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    window.show_ihc_layout()
    app.processEvents()

    assert window.ihc_workspace.canvas_stack.currentWidget() is window.ihc_workspace.layout_canvas

    window.set_module("qpcr")
    window.set_module("ihc")
    app.processEvents()

    assert window.data_panel.ihc_context_stack.currentIndex() == 3
    assert window.ihc_workspace.canvas_stack.currentWidget() is window.ihc_workspace.layout_canvas
    assert window.ihc_workspace.canvas_tools_group.isHidden() is False
    assert window.ihc_workspace.active_image_group.isHidden() is False
    assert window.data_panel.ihc_workflow_group.isHidden() is True

    _close_widget(window, app)


def test_ihc_layout_step_opens_editing_tools_without_measurement(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "ihc_layout_no_measure.png")

    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.set_ihc_workflow_step(3)
    app.processEvents()

    assert window.ihc_workspace.canvas_stack.currentWidget() is window.ihc_workspace.layout_canvas
    assert window.ihc_workspace.canvas_tools_group.isHidden() is False
    controller = window.layout_edit_controller
    assert controller.canvas is window.ihc_workspace.layout_canvas
    assert controller.tools.bar.properties.isVisibleTo(window.v9_shell.stagebar)
    controller.tools.bar.properties.click()
    assert controller.dialog.isVisible()
    assert window.ihc_workspace.workflow_group.isHidden() is True

    _close_widget(window, app)


def test_set_module_brightfield_uses_dedicated_workspace_and_hides_top_data_panel() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("brightfield")

    assert window.active_module == "brightfield"
    assert window.data_panel.isHidden()
    assert window.canvas_stack.currentWidget() is window.brightfield_workspace
    assert window.brightfield_workspace.layout().count() == 1
    assert window.brightfield_workspace.layout().itemAt(0).widget() is window.brightfield_workspace.canvas_stack
    assert window.brightfield_workspace.workflow_panel.parentWidget() is window.v9_shell.right_panel
    assert window.brightfield_workspace.side_panel is window.brightfield_workspace.workflow_panel
    assert not window.workflow_ui.footers['brightfield'].primary.isHidden()
    assert window.workflow_ui.footers['brightfield'].primary.text() == window.brightfield_workspace.import_button.text()
    assert window.brightfield_workspace.active_image_group.isHidden() is False
    assert window.data_panel.brightfield_workflow_group.isHidden() is True

    _close_widget(window, app)


def test_brightfield_module_switch_restores_layout_canvas_when_layout_step_is_active(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_layout_restore.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    window.show_brightfield_layout()
    app.processEvents()

    assert window.brightfield_workspace.canvas_stack.currentWidget() is window.brightfield_workspace.layout_canvas

    window.set_module("qpcr")
    window.set_module("brightfield")
    app.processEvents()

    assert window.data_panel.brightfield_context_stack.currentIndex() == 4
    assert window.brightfield_workspace.canvas_stack.currentWidget() is window.brightfield_workspace.layout_canvas
    assert window.brightfield_workspace.canvas_tools_group.isHidden() is False
    assert window.brightfield_workspace.active_image_group.isHidden() is False
    assert window.data_panel.brightfield_workflow_group.isHidden() is True

    _close_widget(window, app)


def test_brightfield_layout_step_opens_editing_tools_without_measurement(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_layout_no_measure.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.set_brightfield_workflow_step(4)
    app.processEvents()

    assert window.brightfield_workspace.canvas_stack.currentWidget() is window.brightfield_workspace.layout_canvas
    assert window.brightfield_workspace.canvas_tools_group.isHidden() is False
    controller = window.layout_edit_controller
    assert controller.canvas is window.brightfield_workspace.layout_canvas
    assert controller.tools.bar.properties.isVisibleTo(window.v9_shell.stagebar)
    controller.tools.bar.properties.click()
    assert controller.dialog.isVisible()
    assert window.brightfield_workspace.workflow_group.isHidden() is True

    _close_widget(window, app)


def test_set_module_brightfield_shows_workflow_controls() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("brightfield")
    window.show()
    app.processEvents()

    assert window.brightfield_workspace.isVisible()
    assert window.data_panel.brightfield_context_panel.isVisible()
    assert [button.text() for button in window.data_panel.brightfield_step_buttons] == [
        "Import",
        "Preprocess/Segment",
        "Measure",
        "Stats",
        "Layout",
    ]
    assert [window.data_panel.brightfield_stain_type_combo.itemText(index) for index in range(window.data_panel.brightfield_stain_type_combo.count())] == [
        "H&E",
        "Special stain",
        "Crystal violet",
        "Oil Red O",
        "Other high-contrast stain",
    ]
    assert window.data_panel.brightfield_context_stack.currentIndex() == 0
    assert window.data_panel.brightfield_active_image_combo is not None
    assert window.brightfield_workspace.active_image_group.isHidden() is False
    assert window.data_panel.brightfield_workflow_group.isHidden() is True

    window.set_brightfield_workflow_step(2)
    assert window.brightfield_workspace.active_image_group.isHidden() is False
    assert window.data_panel.brightfield_workflow_group.isHidden() is True
    window.data_panel.brightfield_measure_type_combo.setCurrentIndex(1)
    assert window.data_panel.brightfield_workflow_group.isHidden() is False

    _close_widget(window, app)


def test_step6_import_copy_mode_labels_and_status_copy_are_visible(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "step6_copy.png")

    window.set_module("ihc")
    window.show()
    app.processEvents()

    expected_import = tr("histology_import_formats")
    assert expected_import == (
        "Supported import: TIFF, OME-TIFF, PNG, JPEG, BMP, and WebP. "
        "For CZI or ND2, export to OME-TIFF first in ZEN or Nikon software."
    )
    assert IHC_IMAGE_SUFFIXES == {".tif", ".tiff", ".ome.tif", ".ome.tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    assert BRIGHTFIELD_IMAGE_SUFFIXES == IHC_IMAGE_SUFFIXES
    assert ".czi" not in IHC_IMAGE_SUFFIXES
    assert ".nd2" not in IHC_IMAGE_SUFFIXES
    assert expected_import in window.data_panel.ihc_import_hint_label.text()
    assert "For CZI or ND2, export to OME-TIFF first" in window.data_panel.ihc_import_hint_label.text()
    assert "export to OME-TIFF first" in window.ihc_workspace.import_button.toolTip()
    assert window.data_panel.ihc_measure_actions_group.title() == "Foolproof Mode"

    window.data_panel.set_ihc_guided_mode("assisted")
    app.processEvents()
    assert window.data_panel.ihc_measure_actions_group.title() == "Assisted Mode"

    window.data_panel.set_ihc_guided_mode("expert")
    app.processEvents()
    assert window.data_panel.ihc_measure_actions_group.title() == "Expert Mode"

    window.import_ihc_images([str(image_path)])
    window.run_ihc_analysis()
    _wait_for_analysis(window, "ihc")
    ihc_measurement = window._active_ihc_panel().analysis["ihc_measurements"][-1]
    ihc_measurement["warnings"] = ["Review threshold shift before batch comparison."]
    ihc_measurement["reliability"] = {
        "status": "WARN",
        "include_in_statistics": True,
        "messages": ["Review threshold shift before batch comparison."],
    }
    ihc_measurement["reliability_status"] = "WARN"
    window.select_ihc_measurement_result(ihc_measurement["measurement_uuid"])
    app.processEvents()

    assert "Measurement Status: WARN" in window.data_panel.ihc_threshold_status_label.text()
    assert "Status: WARN" in window.data_panel.ihc_summary_label.text()

    window.set_module("brightfield")
    app.processEvents()

    assert expected_import in window.data_panel.brightfield_import_hint_label.text()
    assert "For CZI or ND2, export to OME-TIFF first" in window.data_panel.brightfield_import_hint_label.text()
    assert "export to OME-TIFF first" in window.brightfield_workspace.import_button.toolTip()
    assert window.data_panel.brightfield_measure_group.title() == "Measure"

    window.data_panel.set_brightfield_guided_mode("expert")
    app.processEvents()
    assert window.data_panel.brightfield_measure_group.title() == "Measure"
    assert window.data_panel.current_brightfield_guided_mode() == 'expert'

    window.import_brightfield_images([str(image_path)])
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    brightfield_measurement = window._active_brightfield_panel().analysis["brightfield_measurements"][-1]
    brightfield_measurement["warnings"] = ["Overlay generation failed or has not been confirmed."]
    brightfield_measurement["reliability"] = {
        "status": "FAIL",
        "include_in_statistics": False,
        "messages": ["Overlay generation failed or has not been confirmed."],
    }
    brightfield_measurement["reliability_status"] = "FAIL"
    window.select_brightfield_measurement_result(brightfield_measurement["measurement_uuid"])
    app.processEvents()

    assert "Measurement Status: FAIL" in window.data_panel.brightfield_measure_status_label.text()
    assert "Status: FAIL" in window.data_panel.brightfield_summary_label.text()

    _close_widget(window, app)


def test_import_brightfield_images_sets_active_image_and_preview(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield-sample.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])

    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    assert active_image is not None
    assert active_image["source_name"] == image_path.name
    assert window.brightfield_workspace.canvas.image_label is not None
    assert window.brightfield_workspace.canvas._source_pixmap is not None

    _close_widget(window, app)


def test_brightfield_workspace_import_button_uses_import_helper(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield-import-button.png")

    window.set_module("brightfield")
    window.show()
    app.processEvents()

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getOpenFileNames",
        lambda *args, **kwargs: ([str(image_path)], ""),
    )

    window.brightfield_workspace.import_button.click()
    app.processEvents()

    active_image = window._active_brightfield_panel().active_image()
    assert active_image is not None
    assert active_image["source_name"] == image_path.name

    _close_widget(window, app)


def test_brightfield_workspace_drop_imports_supported_raster(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "brightfield-drop.png")

    window.set_module("brightfield")
    event = _DropEvent(_mime_for(image_path))

    window.brightfield_workspace.dropEvent(event)
    app.processEvents()

    active_image = window._active_brightfield_panel().active_image()
    assert event.accepted
    assert active_image is not None
    assert active_image["source_name"] == image_path.name

    _close_widget(window, app)


def test_brightfield_direct_path_runs_measurement_stats_and_composer(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    generated_asset_dir = tmp_path / "generated_assets"
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(generated_asset_dir))
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_a = _write_synthetic_ihc_png(tmp_path / "bf_control.png")
    image_b = _write_synthetic_ihc_png(tmp_path / "bf_treated.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_a), str(image_b)])
    window.select_brightfield_image(window._active_brightfield_panel().images()[0]["uid"])
    window.data_panel.brightfield_group_edit.setText("Control")
    window.data_panel.brightfield_stain_type_combo.setCurrentText("H&E")
    window.update_brightfield_image_metadata()
    window.data_panel.brightfield_segmentation_mode_combo.setCurrentIndex(0)
    window.preview_brightfield_segmentation()
    window.set_brightfield_workflow_step(2)
    window.set_brightfield_roi_from_canvas(QRect(0, 0, 3, 3))
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    second_uid = window._active_brightfield_panel().images()[1]["uid"]
    window.select_brightfield_image(second_uid)
    window.data_panel.brightfield_group_edit.setText("Treatment")
    window.data_panel.brightfield_stain_type_combo.setCurrentText("Oil Red O")
    window.update_brightfield_image_metadata()
    window.set_brightfield_workflow_step(2)
    window.set_brightfield_roi_from_canvas(QRect(0, 0, 3, 3))
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert window.data_panel.brightfield_results_table.rowCount() == 2
    table = window.data_panel.brightfield_results_table
    table.selectRow(0)
    app.processEvents()
    assert window.brightfield_workspace.current_roi() == QRect(0, 0, 3, 3)

    window.data_panel.brightfield_stats_group_table.item(0, 1).setText("Control")
    window.data_panel.brightfield_stats_group_table.item(1, 1).setText("Treatment")
    window.data_panel.brightfield_stats_reference_group_combo.setCurrentText("Control")
    window.run_brightfield_statistics()

    assert window.data_panel.brightfield_stats_result_table.rowCount() == 2
    active_measurement = window._active_brightfield_measurement(window._active_brightfield_panel())
    assert active_measurement is not None
    active_measurement["warnings"] = ["Confirm segmentation before export."]
    active_measurement["qc_messages"] = ["Mask preview uses the current segmentation mode and active ROI."]
    active_measurement["reliability"] = {
        "status": "WARN",
        "include_in_statistics": True,
        "messages": ["Confirm segmentation before export."],
    }
    active_measurement["reliability_status"] = "WARN"
    created = window.add_brightfield_outputs_to_composer()

    assert len(created) == 2
    overlay_panel = next(
        panel for panel in created if isinstance(panel, ExternalImagePanel) and panel.data.get("source_module") == "brightfield_overlay"
    )
    stats_panel = next(
        panel for panel in created if isinstance(panel, ExternalImagePanel) and panel.data.get("source_module") == "brightfield_statistics"
    )
    assert overlay_panel.data["reliability_status"] == "WARN"
    assert overlay_panel.data["warnings"] == ["Confirm segmentation before export."]
    assert overlay_panel.data["qc_messages"] == ["Mask preview uses the current segmentation mode and active ROI."]
    assert Path(overlay_panel.data["image_path"]).exists()
    assert Path(stats_panel.data["image_path"]).exists()
    assert stats_panel.data["source_format"] == "SVG"
    assert Path(stats_panel.data["image_path"]).suffix.lower() == ".svg"
    assert window.data_panel.brightfield_stats_summary_label.text()

    _close_widget(window, app)


def test_brightfield_stats_use_image_level_samples_when_one_image_has_multiple_rois(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_a = _write_synthetic_ihc_png(tmp_path / "bf_control_a.png")
    image_b = _write_synthetic_ihc_png(tmp_path / "bf_control_b.png")
    image_c = _write_synthetic_ihc_png(tmp_path / "bf_treated_a.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_a), str(image_b), str(image_c)])

    panel = window._active_brightfield_panel()
    image_uids = [image["uid"] for image in panel.images()]

    window.select_brightfield_image(image_uids[0])
    window.data_panel.brightfield_group_edit.setText("Control")
    window.update_brightfield_image_metadata()
    window.set_brightfield_workflow_step(2)
    window.set_brightfield_roi_from_canvas(QRect(0, 0, 3, 3))
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    window.set_brightfield_workflow_step(2)
    window.set_brightfield_roi_from_canvas(QRect(0, 0, 6, 6))
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    window.select_brightfield_image(image_uids[1])
    window.data_panel.brightfield_group_edit.setText("Control")
    window.update_brightfield_image_metadata()
    window.set_brightfield_workflow_step(2)
    window.set_brightfield_roi_from_canvas(QRect(0, 0, 3, 3))
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    window.select_brightfield_image(image_uids[2])
    window.data_panel.brightfield_group_edit.setText("Treatment")
    window.update_brightfield_image_metadata()
    window.set_brightfield_workflow_step(2)
    window.set_brightfield_roi_from_canvas(QRect(0, 0, 3, 3))
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    assert len(window._active_brightfield_panel().analysis["brightfield_measurements"]) == 4
    assert window.data_panel.brightfield_results_table.rowCount() == 4
    assert window.data_panel.brightfield_stats_group_table.rowCount() == 3
    assert [window.data_panel.brightfield_stats_group_table.item(row, 0).text() for row in range(3)] == [
        image_a.name,
        image_b.name,
        image_c.name,
    ]

    window.data_panel.brightfield_stats_group_table.item(0, 1).setText("Control")
    window.data_panel.brightfield_stats_group_table.item(1, 1).setText("Control")
    window.data_panel.brightfield_stats_group_table.item(2, 1).setText("Treatment")
    window.data_panel.brightfield_stats_reference_group_combo.setCurrentText("Control")
    window.run_brightfield_statistics()

    assert {window.data_panel.brightfield_stats_result_table.item(row, 0).text(): window.data_panel.brightfield_stats_result_table.item(row, 1).text() for row in range(window.data_panel.brightfield_stats_result_table.rowCount())} == {
        "Control": "2",
        "Treatment": "1",
    }

    _close_widget(window, app)


def test_brightfield_ai_result_visible_after_run_or_persistence(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_visible.png")
    info_calls: list[tuple[str, str]] = []

    def capture_information(_parent, title, message):
        info_calls.append((title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(main_window_module.QMessageBox, "information", capture_information)
    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window._update_brightfield_he_fiber_calibration_status(panel)
    window.run_brightfield_ai_object_count()
    count = int(panel.analysis["brightfield_ai_object_count_measurements"][-1]["results"]["ai_object_count"])

    assert info_calls and info_calls[-1][0] == "H&E Fiber Area"
    assert window.data_panel.brightfield_results_table.rowCount() == 0
    assert window.data_panel.brightfield_ai_results_table.rowCount() == 1
    assert window.data_panel.brightfield_ai_results_table.horizontalHeaderItem(3).text() == "H&E Fiber Count"
    assert window.data_panel.brightfield_ai_results_table.item(0, 3).text() == str(count)
    assert window.data_panel.brightfield_ai_results_table.item(0, 7).text() == "Yes"
    assert "Latest H&E Fiber Area result" in window.data_panel.brightfield_ai_summary_label.text()
    assert f"H&E Fiber Count: {count}" in window.data_panel.brightfield_ai_summary_label.text()
    assert str(count) in window.data_panel.brightfield_ai_results_table_tsv()

    _close_widget(window, app)


def test_brightfield_ai_object_count_warning_when_no_active_image(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    warning_calls: list[tuple[str, str]] = []

    def capture_warning(_parent, title, message):
        warning_calls.append((title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(main_window_module.QMessageBox, "warning", capture_warning)

    window.set_module("brightfield")
    window.run_brightfield_ai_object_count()

    assert warning_calls == [
        (
            "Brightfield",
            "Import a Brightfield H&E image before running H&E Fiber Area.",
        )
    ]

    _close_widget(window, app)


def test_brightfield_he_fiber_area_warns_when_not_calibrated(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_not_calibrated.png")
    warning_calls: list[tuple[str, str]] = []

    def capture_warning(_parent, title, message):
        warning_calls.append((title, message))
        return QMessageBox.StandardButton.Ok

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", capture_warning)

    window.run_brightfield_ai_object_count()

    assert warning_calls == [("H&E Fiber Area", "Calibrate H&E Fiber Area for the active image before running measurement.")]

    _close_widget(window, app)


def test_brightfield_he_fiber_calibration_dialog_updates_main_status(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_calibration_dialog.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    assert "not calibrated" in window.data_panel.brightfield_he_fiber_calibration_status_label.text()

    def run_dialog_action(dialog) -> int:
        button = next(
            item
            for item in dialog.findChildren(QPushButton)
            if item.text() == "Calibrate Active Image"
        )
        button.click()
        return 0

    monkeypatch.setattr(main_window_module.QDialog, "exec", run_dialog_action)
    window.calibrate_brightfield_he_fiber_area()

    assert "calibrated" in window.data_panel.brightfield_he_fiber_calibration_status_label.text()
    assert window._active_brightfield_panel().he_fiber_area_calibration() is not None

    _close_widget(window, app)


def test_brightfield_he_fiber_area_does_not_run_without_calibration_even_if_model_missing(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_model_missing.png")
    warning_calls: list[tuple[str, str]] = []

    def capture_warning(_parent, title, message):
        warning_calls.append((title, message))
        return QMessageBox.StandardButton.Ok

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", capture_warning)

    window.run_brightfield_ai_object_count()

    assert warning_calls == [("H&E Fiber Area", "Calibrate H&E Fiber Area for the active image before running measurement.")]

    _close_widget(window, app)


def test_brightfield_he_fiber_area_does_not_run_without_calibration_even_if_manifest_missing(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_manifest_missing.png")
    warning_calls: list[tuple[str, str]] = []

    def capture_warning(_parent, title, message):
        warning_calls.append((title, message))
        return QMessageBox.StandardButton.Ok

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", capture_warning)

    window.run_brightfield_ai_object_count()

    assert warning_calls == [("H&E Fiber Area", "Calibrate H&E Fiber Area for the active image before running measurement.")]

    _close_widget(window, app)


def test_brightfield_ai_object_count_success_calls_existing_refresh_path(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    refreshed_measurements: list[dict] = []

    def capture_refresh(self, measurement):
        refreshed_measurements.append(measurement)

    monkeypatch.setattr(main_window_module.MainWindow, "_refresh_brightfield_after_ai_measurement", capture_refresh)

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "bf_ai_refresh_path.png")
    expected_measurement = {"results": {"ai_object_count": 9}, "warnings": []}

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    monkeypatch.setattr(panel, "create_ai_object_count_measurement", lambda **kwargs: expected_measurement)

    window.run_brightfield_ai_object_count()

    assert refreshed_measurements == [expected_measurement]

    _close_widget(window, app)


def test_brightfield_he_fiber_area_success_text(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_success_text.png")
    info_calls: list[tuple[str, str]] = []

    def capture_information(_parent, title, message):
        info_calls.append((title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(main_window_module.QMessageBox, "information", capture_information)
    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window.run_brightfield_ai_object_count()
    measurement = panel.analysis["brightfield_ai_object_count_measurements"][-1]
    count = int(measurement["results"]["ai_object_count"])
    mean_area = float(measurement["results"]["ai_object_mean_area_px"])

    assert info_calls[-1] == (
        "H&E Fiber Area",
        f"H&E fiber count: {count} objects.\n"
        f"Mean fiber area: {mean_area:.2f} px².\n"
        "Warnings:\n"
        "- None",
    )

    _close_widget(window, app)


def test_brightfield_ai_statistics_are_separate_from_traditional_statistics(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_stats.png")
    banner_capture: dict[str, object] = {}

    def capture_banner(status, warnings, metrics=None):
        banner_capture["status"] = status
        banner_capture["warnings"] = list(warnings)
        banner_capture["metrics"] = list(metrics or [])

    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(window.data_panel.brightfield_reliability_banner, "set_status", capture_banner)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    window.run_brightfield_ai_object_count()
    ai_measurement = panel.analysis["brightfield_ai_object_count_measurements"][-1]
    ai_count_text = f"{float(ai_measurement['results']['ai_object_count']):.3f}"

    assert ai_measurement["include_in_statistics"] is True
    assert panel.analysis["brightfield_ai_object_count_statistics"]["source"] == "brightfield_ai_object_count"
    assert panel.analysis["brightfield_ai_object_count_statistics"]["n_measurements"] == 1
    assert window.data_panel.brightfield_ai_stats_result_table.rowCount() == 1
    assert window.data_panel.brightfield_results_table.horizontalHeaderItem(5).text() == "Segmented Regions"
    assert "Segmented Regions" in window.data_panel.brightfield_results_table_tsv().splitlines()[0]
    assert "Object Count" not in window.data_panel.brightfield_results_table_tsv().splitlines()[0]
    assert "Segmented Regions:" in window.data_panel.brightfield_summary_label.text()
    assert "Object Count:" not in window.data_panel.brightfield_summary_label.text()
    assert banner_capture["metrics"][1][0] == "Segmented Regions"
    assert window.data_panel.brightfield_ai_stats_result_table.item(0, 2).text() == ai_count_text
    assert window.data_panel.brightfield_stats_plot_source() == "traditional"
    assert window.data_panel.brightfield_copy_stats_button.text() == "Copy Traditional Stats"
    assert window.data_panel.brightfield_copy_ai_stats_button.text() == "Copy H&E Fiber Stats"
    window.run_brightfield_statistics()
    assert panel.analysis["brightfield_statistics"]["source"] == "brightfield_measurement"
    assert all(
        ai_measurement["measurement_uuid"] not in row.get("measurement_uuids", [])
        for row in panel.analysis["brightfield_statistics"].get("rows", [])
    )
    traditional_tsv = window.data_panel.brightfield_statistics_table_tsv()
    traditional_clipboard = QApplication.clipboard()
    window.copy_brightfield_statistics_table()
    assert traditional_clipboard.text() == traditional_tsv
    traditional_csv = tmp_path / "traditional_brightfield_stats.csv"
    window.export_brightfield_statistics_csv_to_path(traditional_csv)
    assert traditional_csv.read_text(encoding="utf-8").startswith("Group,n,Mean,SEM")

    window.data_panel.brightfield_stats_plot_source_combo.setCurrentIndex(1)
    app.processEvents()
    assert window.data_panel.brightfield_stats_plot_source() == "ai_object_count"
    assert window.data_panel.brightfield_stats_metric_combo.currentText() == "Segmented Regions"
    assert window.data_panel.brightfield_stats_metric_combo.currentData() == "Segmented Region Count"
    assert window.data_panel.current_brightfield_stats_metric() == "Segmented Region Count"
    assert window.data_panel.brightfield_stats_metric_combo.isEnabled() is False

    window.run_brightfield_statistics()
    ai_plot_stats = panel.analysis["brightfield_ai_object_count_plot_statistics"]
    assert ai_plot_stats["source"] == "brightfield_ai_object_count"
    assert ai_plot_stats["metric"] == "H&E Fiber Count"
    assert [row["measurement_uuid"] for row in ai_plot_stats["plot_data"]] == [ai_measurement["measurement_uuid"]]
    assert window.data_panel.brightfield_stats_result_table.rowCount() == 1
    assert window.data_panel.brightfield_stats_result_table.item(0, 2).text() == ai_count_text
    assert "H&E Fiber Area statistics" in window.data_panel.brightfield_stats_summary_label.text()
    window.copy_brightfield_ai_statistics_table()
    assert ai_count_text in traditional_clipboard.text()
    ai_csv = tmp_path / "ai_object_count_stats.csv"
    window.export_brightfield_ai_statistics_csv_to_path(ai_csv)
    assert ai_csv.read_text(encoding="utf-8").startswith("Group,n,Mean,SEM")
    assert panel.analysis["brightfield_statistics"]["source"] == "brightfield_measurement"
    assert window.data_panel.brightfield_ai_stats_result_table.rowCount() == 1
    assert window.data_panel.brightfield_ai_stats_result_table.item(0, 2).text() == ai_count_text
    assert "H&E Fiber Area statistics" in window.data_panel.brightfield_ai_stats_summary_label.text()

    _close_widget(window, app)


def test_brightfield_he_fiber_area_overlay_toggle_survives_language_switch(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_overlay_toggle.png")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window.run_brightfield_ai_object_count()

    assert window.brightfield_workspace.canvas.current_preview_mode() == "overlay"
    assert window.brightfield_workspace.overlay_preview_button.isEnabled()
    assert window.brightfield_workspace.overlay_preview_button.isChecked()
    window.brightfield_workspace.original_preview_button.click()
    assert window.brightfield_workspace.canvas.current_preview_mode() == "original"
    window.brightfield_workspace.overlay_preview_button.click()
    assert window.brightfield_workspace.canvas.current_preview_mode() == "overlay"

    window.switch_language("zh")

    assert window.brightfield_workspace.canvas.current_preview_mode() == "overlay"
    assert window.brightfield_workspace.overlay_preview_button.isEnabled()
    assert window.brightfield_workspace.overlay_preview_button.isChecked()

    window.switch_language("en")
    _close_widget(window, app)


def test_ihc_and_brightfield_image_switchers_work_outside_import_step(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    ihc_first = _write_synthetic_ihc_png(tmp_path / "ihc_first.png")
    ihc_second = _write_synthetic_ihc_png(tmp_path / "ihc_second.png")
    bf_first = _write_synthetic_brightfield_he_png(tmp_path / "bf_first.png")
    bf_second = _write_synthetic_brightfield_he_png(tmp_path / "bf_second.png")

    window.set_module("ihc")
    window.import_ihc_images([str(ihc_first), str(ihc_second)])
    ihc_panel = window._active_ihc_panel()
    window.data_panel.set_ihc_step(1)
    second_ihc_uid = str(ihc_panel.images()[1]["uid"])
    window.data_panel.ihc_active_image_combo.setCurrentIndex(
        window.data_panel.ihc_active_image_combo.findData(second_ihc_uid)
    )
    app.processEvents()

    assert str(ihc_panel.active_image()["uid"]) == second_ihc_uid

    window.set_module("brightfield")
    window.import_brightfield_images([str(bf_first), str(bf_second)])
    brightfield_panel = window._active_brightfield_panel()
    window.data_panel.set_brightfield_step(2)
    second_bf_uid = str(brightfield_panel.images()[1]["uid"])
    window.data_panel.brightfield_active_image_combo.setCurrentIndex(
        window.data_panel.brightfield_active_image_combo.findData(second_bf_uid)
    )
    app.processEvents()

    assert str(brightfield_panel.active_image()["uid"]) == second_bf_uid

    _close_widget(window, app)


def test_ihc_and_brightfield_import_controls_use_add_remove_not_preview_layout(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    ihc_first = _write_synthetic_ihc_png(tmp_path / "ihc_remove_first.png")
    ihc_second = _write_synthetic_ihc_png(tmp_path / "ihc_remove_second.png")
    bf_first = _write_synthetic_brightfield_he_png(tmp_path / "bf_remove_first.png")
    bf_second = _write_synthetic_brightfield_he_png(tmp_path / "bf_remove_second.png")

    window.set_module("ihc")
    assert window.ihc_workspace.import_button.parentWidget() is not None
    assert window.ihc_workspace.remove_button.parentWidget() is not None
    assert window.ihc_workspace.preview_button.parentWidget() is None
    assert window.ihc_workspace.layout_button.parentWidget() is None
    window.import_ihc_images([str(ihc_first), str(ihc_second)])
    window.ihc_workspace.remove_button.click()
    app.processEvents()
    assert len(window._active_ihc_panel().images()) == 1

    window.set_module("brightfield")
    assert window.brightfield_workspace.import_button.parentWidget() is not None
    assert window.brightfield_workspace.remove_button.parentWidget() is not None
    assert window.brightfield_workspace.preview_button.parentWidget() is None
    assert window.brightfield_workspace.layout_button.parentWidget() is None
    window.import_brightfield_images([str(bf_first), str(bf_second)])
    assert "Scale:" in window.data_panel.brightfield_he_fiber_scale_status_label.text()
    window.brightfield_workspace.remove_button.click()
    app.processEvents()
    assert len(window._active_brightfield_panel().images()) == 1
    assert window.data_panel.brightfield_he_fiber_draw_scale_line_button.text() == "Draw Scale"

    _close_widget(window, app)


def test_brightfield_layout_preview_toggle_returns_to_requested_original_or_overlay(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "layout_toggle.png")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    image_uid = str(panel.active_image()["uid"])
    panel.calibrate_he_fiber_area(image_uid)
    window.run_brightfield_ai_object_count()
    window.show_brightfield_layout()

    assert window.brightfield_workspace.canvas_stack.currentWidget() is window.brightfield_workspace.layout_canvas

    window.brightfield_workspace.original_preview_button.click()
    app.processEvents()
    assert window.brightfield_workspace.canvas_stack.currentWidget() is window.brightfield_workspace.canvas
    assert window.brightfield_workspace.canvas.current_preview_mode() == "original"

    window.show_brightfield_layout()
    window.brightfield_workspace.overlay_preview_button.click()
    app.processEvents()
    assert window.brightfield_workspace.canvas_stack.currentWidget() is window.brightfield_workspace.canvas
    assert window.brightfield_workspace.canvas.current_preview_mode() == "overlay"

    _close_widget(window, app)


def test_brightfield_measurement_scale_line_drag_updates_saved_line(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "scale_drag.png")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    image_uid = str(panel.active_image()["uid"])

    window.set_brightfield_measurement_scale_line({"x1": 10.0, "y1": 20.0, "x2": 90.0, "y2": 20.0})
    window.preview_brightfield_measurement_scale_line({"x1": 15.0, "y1": 25.0, "x2": 95.0, "y2": 25.0})
    window.set_brightfield_measurement_scale_line({"x1": 15.0, "y1": 25.0, "x2": 95.0, "y2": 25.0})

    scale = panel.measurement_scale_bar(image_uid)
    assert scale["line_px"] == {"x1": 15.0, "y1": 25.0, "x2": 95.0, "y2": 25.0}
    assert scale["calibration_source"] == "manual_line"

    _close_widget(window, app)


def test_brightfield_he_fiber_frequency_chart_uses_um2_axis_after_scale_calibration(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_um_axis.png")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window.set_brightfield_measurement_scale_line({"x1": 0.0, "y1": 0.0, "x2": 100.0, "y2": 0.0})
    window.run_brightfield_ai_object_count()

    window.data_panel.brightfield_stats_plot_source_combo.setCurrentIndex(
        window.data_panel.brightfield_stats_plot_source_combo.findData("ai_object_count")
    )
    window.data_panel.brightfield_stats_chart_mode_combo.setCurrentIndex(
        window.data_panel.brightfield_stats_chart_mode_combo.findData("frequency_bar")
    )
    payload = window._brightfield_chart_export_payload(panel, {})
    figure = window._build_brightfield_chart_figure(panel, payload)

    assert payload["unit"] == "um^2"
    assert figure.axes[0].xaxis.label.get_text() == "Fiber area (um²)"

    _close_widget(window, app)


def test_main_window_button_readability_policy_keeps_compact_actions_accessible() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    for button in (
        window.data_panel.brightfield_he_fiber_draw_scale_line_button,
        window.data_panel.brightfield_he_fiber_apply_scale_button,
        window.data_panel.brightfield_he_fiber_refresh_results_button,
        window.data_panel.brightfield_he_fiber_reset_scale_button,
    ):
        assert button.minimumWidth() == 0
        assert button.toolTip()

    _close_widget(window, app)


def test_brightfield_scale_actions_use_compact_labels_and_refresh_tooltip() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    controls = window.data_panel
    assert controls.brightfield_he_fiber_draw_scale_line_button.text() == "Draw Scale"
    assert controls.brightfield_he_fiber_apply_scale_button.text() == "Apply Scale"
    assert controls.brightfield_he_fiber_refresh_results_button.text() == "Refresh"
    assert "Refresh H&E Fiber Area results" in controls.brightfield_he_fiber_refresh_results_button.toolTip()
    assert controls.brightfield_he_fiber_reset_scale_button.text() == "Reset"

    _close_widget(window, app)


def test_brightfield_he_fiber_table_lists_each_area_and_reopens_matching_overlay(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    first_path = _write_synthetic_he_fiber_area_png(tmp_path / "fiber_first.png")
    second_path = _write_synthetic_he_fiber_area_png(tmp_path / "fiber_second.png")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(first_path), str(second_path)])
    panel = window._active_brightfield_panel()
    first_uid = str(panel.images()[0]["uid"])
    second_uid = str(panel.images()[1]["uid"])
    for image_uid in (first_uid, second_uid):
        window.select_brightfield_image(image_uid)
        panel.calibrate_he_fiber_area(image_uid)
        window.run_brightfield_ai_object_count()

    first_measurement = panel.analysis["brightfield_ai_object_count_measurements"][0]
    first_areas = first_measurement["results"]["ai_object_areas_px"]
    window.data_panel.brightfield_ai_results_table.clearSelection()
    window.data_panel.brightfield_ai_results_table.selectRow(0)
    app.processEvents()
    assert window.data_panel.brightfield_ai_fiber_area_table.rowCount() == len(first_areas)
    assert window.data_panel.brightfield_ai_fiber_area_table.item(0, 3).text() == str(first_areas[0])

    window.brightfield_workspace.original_preview_button.click()
    assert window.brightfield_workspace.canvas.current_preview_mode() == "original"
    window.data_panel.brightfield_ai_results_table.clearSelection()
    window.data_panel.brightfield_ai_results_table.selectRow(0)
    app.processEvents()

    assert str(panel.active_image()["uid"]) == first_uid
    assert window.brightfield_workspace.canvas.current_preview_mode() == "overlay"

    _close_widget(window, app)


def test_main_window_blocks_wheel_changes_for_closed_combo_boxes(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    combo = window.data_panel.brightfield_stats_plot_source_combo

    assert window.eventFilter(combo, _wheel_event()) is True
    QApplication.sendEvent(combo, _wheel_event())
    assert combo.currentIndex() == 0

    _close_widget(window, app)


def test_main_window_blocks_wheel_changes_for_spin_boxes(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    spin = QSpinBox()
    spin.setRange(0, 10)
    spin.setValue(3)

    assert window.eventFilter(spin, _wheel_event()) is True
    QApplication.sendEvent(spin, _wheel_event())
    assert spin.value() == 3

    _close_widget(window, app)


def test_brightfield_and_fluorescence_distribution_controls_offer_percent_mode(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    assert window.data_panel.brightfield_stats_chart_mode_combo.findData("frequency_bar") >= 0
    assert window.data_panel.brightfield_stats_chart_mode_combo.findData("frequency_line") >= 0
    assert window.data_panel.brightfield_stats_frequency_mode_combo.findData("percent") >= 0
    assert window.fluoro_workspace.fluoro_stats_frequency_mode_combo.findData("percent") >= 0

    _close_widget(window, app)


def test_brightfield_frequency_chart_exports_and_reaches_composer(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "frequency_export.png")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    image_uid = str(panel.active_image()["uid"])
    panel.calibrate_he_fiber_area(image_uid)
    window.run_brightfield_ai_object_count()
    window.data_panel.brightfield_stats_plot_source_combo.setCurrentIndex(
        window.data_panel.brightfield_stats_plot_source_combo.findData("ai_object_count")
    )
    window.run_brightfield_statistics()
    window.data_panel.brightfield_stats_chart_mode_combo.setCurrentIndex(
        window.data_panel.brightfield_stats_chart_mode_combo.findData("frequency_line")
    )
    window.data_panel.brightfield_stats_frequency_mode_combo.setCurrentIndex(
        window.data_panel.brightfield_stats_frequency_mode_combo.findData("percent")
    )
    app.processEvents()

    exported = window.export_brightfield_current_chart_to_path(tmp_path / "fiber_frequency.png")
    created = window.add_brightfield_outputs_to_composer()
    chart_panel = next(item for item in created if item.data.get("export_item_key") == "statistics_chart")

    assert exported.exists()
    assert chart_panel.data["chart_mode"] == "frequency_line"
    assert chart_panel.data["frequency_mode"] == "percent"

    _close_widget(window, app)


def test_brightfield_stats_plot_source_switches_between_traditional_and_ai(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_plot_source.png")
    captured_results: list[dict] = []

    def capture_stats_figure(statistics_result, *args, **kwargs):
        from matplotlib.figure import Figure

        captured_results.append(dict(statistics_result))
        return Figure(figsize=(2, 2), dpi=72)

    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(main_window_module, "build_group_statistics_figure", capture_stats_figure)
    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    window.run_brightfield_ai_object_count()
    ai_measurement = panel.analysis["brightfield_ai_object_count_measurements"][-1]
    ai_measurement_uuid = ai_measurement["measurement_uuid"]
    ai_count = float(ai_measurement["results"]["ai_object_count"])

    assert window.data_panel.brightfield_stats_plot_source_combo.currentData() == "traditional"
    window.run_brightfield_statistics()
    traditional_plot = captured_results[-1]
    assert traditional_plot["source"] == "brightfield_measurement"
    assert all(
        ai_measurement_uuid not in row.get("measurement_uuids", [])
        for row in traditional_plot.get("plot_data", [])
    )

    window.data_panel.brightfield_stats_plot_source_combo.setCurrentIndex(
        window.data_panel.brightfield_stats_plot_source_combo.findData("ai_object_count")
    )
    app.processEvents()
    window.run_brightfield_statistics()
    ai_plot = captured_results[-1]
    assert ai_plot["source"] == "brightfield_ai_object_count"
    assert ai_plot["metric"] == "H&E Fiber Count"
    assert [row["source"] for row in ai_plot["plot_data"]] == ["brightfield_ai_object_count"]
    assert [row["series_label"] for row in ai_plot["plot_data"]] == ["H&E Fiber Count"]
    assert [row["sample_key"] for row in ai_plot["plot_data"]] == [ai_measurement_uuid]
    assert [row["value"] for row in ai_plot["plot_data"]] == [ai_count]

    _close_widget(window, app)


def test_brightfield_ai_statistics_csv_export_is_separate(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_stats_export.png")

    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window.run_brightfield_ai_object_count()
    count_text = f"{float(panel.analysis['brightfield_ai_object_count_measurements'][-1]['results']['ai_object_count']):.3f}"
    window.data_panel.brightfield_stats_plot_source_combo.setCurrentIndex(1)
    app.processEvents()
    window.run_brightfield_statistics()

    ai_csv = tmp_path / "ai_stats.csv"
    traditional_csv = tmp_path / "traditional_stats.csv"
    window.export_brightfield_ai_statistics_csv_to_path(ai_csv)
    window.export_brightfield_statistics_csv_to_path(traditional_csv)

    assert ai_csv.read_text(encoding="utf-8").startswith("Group,n,Mean,SEM")
    assert count_text in ai_csv.read_text(encoding="utf-8")
    assert traditional_csv.read_text(encoding="utf-8").startswith("Group,n,Mean,SEM")
    assert count_text not in traditional_csv.read_text(encoding="utf-8")

    _close_widget(window, app)


def test_brightfield_ai_copy_and_export_use_summary_before_plot_statistics(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_summary_copy_export.png")

    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window.run_brightfield_ai_object_count()
    count_text = f"{float(panel.analysis['brightfield_ai_object_count_measurements'][-1]['results']['ai_object_count']):.3f}"

    assert panel.analysis.get("brightfield_ai_object_count_plot_statistics", {}) == {}
    assert panel.analysis["brightfield_ai_object_count_statistics"]["n_measurements"] == 1
    assert window.data_panel.brightfield_ai_stats_result_table.rowCount() == 1
    assert window.data_panel.brightfield_ai_stats_result_table.item(0, 2).text() == count_text

    window.copy_brightfield_ai_statistics_table()
    clipboard_text = QApplication.clipboard().text()
    assert clipboard_text.startswith("Group\tn\tMean Count\tMedian Count\tMean Area px\tMedian Area px")
    assert count_text in clipboard_text

    ai_csv = tmp_path / "ai_summary_stats.csv"
    window.export_brightfield_ai_statistics_csv_to_path(ai_csv)
    ai_csv_text = ai_csv.read_text(encoding="utf-8")
    assert ai_csv_text.startswith("Group,n,Mean Count,Median Count,Mean Area px,Median Area px")
    assert count_text in ai_csv_text

    _close_widget(window, app)


def test_brightfield_summary_uses_segmented_regions_wording_for_traditional_measurements(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "bf_segmented_regions_summary.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")

    summary = window.data_panel.brightfield_summary_label.text()

    assert "Segmented Regions:" in summary
    assert "Object Count:" not in summary

    _close_widget(window, app)


def test_brightfield_ai_measurement_save_load_populates_ai_results(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_he_fiber_area_png(tmp_path / "bf_he_fiber_roundtrip.png")

    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    panel = window._active_brightfield_panel()
    active_image = panel.active_image()
    panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
    window.run_brightfield_ai_object_count()
    count = int(panel.analysis["brightfield_ai_object_count_measurements"][-1]["results"]["ai_object_count"])
    target = tmp_path / "bf_ai_roundtrip.sfig"
    window.project.save_sfig(target)

    restored = main_window_module.Project.load_sfig(target)
    restored_panel = next(panel for panel in restored.panels if isinstance(panel, BrightfieldPanel))
    assert restored_panel.analysis["brightfield_measurements"] == []
    assert restored_panel.analysis["brightfield_ai_object_count_measurements"][0]["results"]["ai_object_count"] == count
    assert restored_panel.analysis["brightfield_ai_object_count_measurements"][0]["include_in_statistics"] is True

    restored_window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    restored_window.project = restored
    restored_window.set_module("brightfield")

    assert restored_window.data_panel.brightfield_results_table.rowCount() == 0
    assert restored_window.data_panel.brightfield_ai_results_table.rowCount() == 1
    assert restored_window.data_panel.brightfield_ai_results_table.item(0, 3).text() == str(count)
    assert restored_window.data_panel.brightfield_ai_results_table.item(0, 7).text() == "Yes"
    assert restored_window.data_panel.brightfield_ai_stats_result_table.rowCount() == 1
    assert restored_window.data_panel.brightfield_ai_stats_result_table.item(0, 2).text() == f"{float(count):.3f}"
    assert "Latest H&E Fiber Area result" in restored_window.data_panel.brightfield_ai_summary_label.text()
    assert restored_window._active_brightfield_panel().analysis["brightfield_ai_object_count_statistics"]["n_measurements"] == 1

    _close_widget(restored_window, app)
    _close_widget(window, app)


def test_brightfield_roi_updates_canvas_and_status_immediately(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_roi_sync.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    _wait_for_analysis(window, 'import:brightfield')
    window.set_brightfield_workflow_step(2)
    window.set_brightfield_roi_from_canvas(QRect(1, 2, 3, 4))
    app.processEvents()

    assert window.brightfield_workspace.current_roi() == QRect(1, 2, 3, 4)
    assert window.brightfield_workspace.canvas.current_roi_rect() == QRect(1, 2, 3, 4)
    assert window.data_panel.brightfield_roi_status_label.text() == "ROI: Rectangle x=1, y=2, w=3, h=4"

    panel = window._active_brightfield_panel()
    window._refresh_brightfield_preview(panel)
    app.processEvents()

    assert window.brightfield_workspace.current_roi() == QRect(1, 2, 3, 4)
    assert window.data_panel.brightfield_roi_status_label.text() == "ROI: Rectangle x=1, y=2, w=3, h=4"

    _close_widget(window, app)


def test_brightfield_clear_roi_restores_whole_image_status_and_clears_canvas_selection(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_clear_roi.png")

    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    _wait_for_analysis(window, 'import:brightfield')
    window.set_brightfield_workflow_step(2)
    window.set_brightfield_roi_from_canvas(QRect(1, 2, 3, 4))
    app.processEvents()

    assert window.brightfield_workspace.current_roi() == QRect(1, 2, 3, 4)
    assert window.brightfield_workspace.canvas.current_roi_rect() == QRect(1, 2, 3, 4)

    window.data_panel.brightfield_roi_clear_button.click()
    app.processEvents()

    panel = window._active_brightfield_panel()
    assert window.brightfield_workspace.current_roi() is None
    assert window.brightfield_workspace.canvas.current_roi_rect() is None
    assert window.brightfield_workspace.canvas.roi_display_rect() is None
    assert window.data_panel.brightfield_roi_status_label.text() == "ROI: Whole image"
    assert panel.active_roi() is None

    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    measurement = panel.analysis["brightfield_measurements"][-1]
    assert measurement["roi"]["mode"] == "whole_image"
    assert measurement["roi"]["items"] == []

    _close_widget(window, app)


def test_brightfield_roi_round_trip_mapping_respects_letterboxed_offsets(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_tall_preview_png(tmp_path / "brightfield_letterbox_roundtrip.png")

    window.set_module("brightfield")
    window.show()
    window.import_brightfield_images([str(image_path)])
    _wait_for_analysis(window, 'import:brightfield')
    window.set_brightfield_workflow_step(2)
    app.processEvents()

    label = window.brightfield_workspace.canvas.image_label
    assert label is not None
    pixmap_rect = label._pixmap_rect()
    assert pixmap_rect.x() > 0
    selected_display_rect = QRect(pixmap_rect.x() + 24, pixmap_rect.y() + 36, 42, 64)
    mapped_rect = label._map_label_rect_to_source(selected_display_rect)

    window.set_brightfield_roi_from_canvas(mapped_rect)
    app.processEvents()

    visible_rect = window.brightfield_workspace.canvas.roi_display_rect()
    assert visible_rect is not None
    assert window.brightfield_workspace.current_roi() == mapped_rect
    assert window.data_panel.brightfield_roi_status_label.text().startswith("ROI: Rectangle")

    _close_widget(window, app)


def test_brightfield_roi_alignment_survives_refresh_zoom_and_overlay_preview(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_tall_preview_png(tmp_path / "brightfield_refresh_alignment.png")

    window.set_module("brightfield")
    window.show()
    window.import_brightfield_images([str(image_path)])
    _wait_for_analysis(window, 'import:brightfield')
    window.set_brightfield_workflow_step(2)
    app.processEvents()

    label = window.brightfield_workspace.canvas.image_label
    assert label is not None
    selected_display_rect = QRect(label._pixmap_rect().x() + 18, label._pixmap_rect().y() + 28, 36, 70)
    mapped_rect = label._map_label_rect_to_source(selected_display_rect)
    window.set_brightfield_roi_from_canvas(mapped_rect)
    app.processEvents()

    def assert_roi_alignment() -> None:
        current_label = window.brightfield_workspace.canvas.image_label
        assert current_label is not None
        visible_rect = window.brightfield_workspace.canvas.roi_display_rect()
        assert visible_rect is not None
        assert window.brightfield_workspace.current_roi() == mapped_rect
        assert window.data_panel.brightfield_roi_status_label.text().startswith("ROI: Rectangle")

    panel = window._active_brightfield_panel()
    assert_roi_alignment()
    window._refresh_brightfield_preview(panel)
    app.processEvents()
    assert_roi_alignment()
    window.brightfield_workspace.canvas.zoom_by(ZOOM_STEP)
    app.processEvents()
    assert_roi_alignment()
    window.brightfield_workspace.canvas.fit_to_canvas()
    app.processEvents()
    assert_roi_alignment()
    window.preview_brightfield_segmentation()
    app.processEvents()
    assert_roi_alignment()
    window.run_brightfield_analysis()
    _wait_for_analysis(window, "brightfield")
    app.processEvents()
    assert_roi_alignment()

    _close_widget(window, app)


def test_brightfield_image_switch_does_not_show_previous_image_roi(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    first_path = _write_tall_preview_png(tmp_path / "brightfield_roi_first.png")
    second_path = _write_tall_preview_png(tmp_path / "brightfield_roi_second.png")

    window.set_module("brightfield")
    window.show()
    window.import_brightfield_images([str(first_path), str(second_path)])
    _wait_for_analysis(window, 'import:brightfield')
    panel = window._active_brightfield_panel()
    first_uid, second_uid = [image["uid"] for image in panel.images()]

    window.select_brightfield_image(first_uid)
    window.set_brightfield_workflow_step(2)
    app.processEvents()
    label = window.brightfield_workspace.canvas.image_label
    assert label is not None
    mapped_rect = label._map_label_rect_to_source(QRect(label._pixmap_rect().x() + 20, label._pixmap_rect().y() + 24, 32, 68))
    window.set_brightfield_roi_from_canvas(mapped_rect)
    app.processEvents()
    assert window.brightfield_workspace.canvas.roi_display_rect() is not None

    window.select_brightfield_image(second_uid)
    app.processEvents()
    assert window.brightfield_workspace.current_roi() is None
    assert window.brightfield_workspace.canvas.current_roi_rect() is None
    assert window.brightfield_workspace.canvas.roi_display_rect() is None
    assert window.data_panel.brightfield_roi_status_label.text() == "ROI: Whole image"

    _close_widget(window, app)


def test_ihc_workflow_steps_switch_visible_context(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    image_path = _write_synthetic_ihc_png(tmp_path / "workflow_steps.png")

    window.resize(1366, 768)
    window.set_module("ihc")
    window.import_ihc_images([str(image_path)])
    window.show()
    app.processEvents()

    window.data_panel.ihc_step_buttons[0].click()
    app.processEvents()
    assert window.data_panel.ihc_context_stack.currentIndex() == 0
    assert window.data_panel.ihc_active_image_combo.count() == 1
    assert not window.data_panel.ihc_run_analysis_button.isVisible()

    window.data_panel.ihc_step_buttons[1].click()
    app.processEvents()
    assert window.data_panel.ihc_context_stack.currentIndex() == 1
    assert window.data_panel.current_ihc_guided_mode() == "foolproof"
    assert window.data_panel.ihc_try_assisted_button.isVisible()
    assert not window.data_panel.ihc_positive_threshold_spin.isVisible()
    assert not window.data_panel.ihc_guided_controls_group.isVisible()
    assert window.data_panel.ihc_roi_draw_button.isVisible()
    assert window.workflow_ui.footers['ihc'].primary.isVisible()
    assert window.workflow_ui.footers['ihc'].source is window.data_panel.ihc_run_analysis_button
    assert window.data_panel.ihc_export_overlay_button.isVisible()
    assert window.data_panel.ihc_results_table.isVisible()
    assert not window.data_panel.ihc_stats_run_button.isVisible()

    window.data_panel.ihc_try_assisted_button.click()
    app.processEvents()
    assert window.data_panel.current_ihc_guided_mode() == "assisted"
    assert window.data_panel.ihc_guided_controls_group.isVisible()
    assert window.data_panel.ihc_mark_positive_button.isVisible()
    assert window.data_panel.ihc_mark_negative_button.isVisible()
    assert window.data_panel.ihc_stop_sampling_button.isVisible()
    assert window.data_panel.ihc_recalibrate_button.isVisible()
    assert window.data_panel.ihc_revert_auto_button.isVisible()
    assert window.data_panel.ihc_delete_example_button.isVisible()
    assert window.data_panel.ihc_examples_list.isVisible()
    assert window.data_panel.ihc_batch_threshold_label.isVisible()
    assert window.data_panel.ihc_overlay_opacity_slider.isVisible()

    window.data_panel.set_ihc_guided_mode("expert")
    app.processEvents()
    assert not window.data_panel.ihc_guided_controls_group.isVisible()
    assert window.data_panel.ihc_positive_threshold_spin.isVisible()

    window.data_panel.ihc_step_buttons[2].click()
    app.processEvents()
    assert window.data_panel.ihc_context_stack.currentIndex() == 2
    assert window.data_panel.ihc_stats_metric_combo.isVisible()
    assert window.data_panel.ihc_stats_group_table.isVisible()
    assert window.workflow_ui.footers['ihc'].primary.isVisible()
    assert window.workflow_ui.footers['ihc'].source is window.data_panel.ihc_stats_run_button
    assert not window.data_panel.ihc_add_outputs_button.isVisible()

    window.data_panel.ihc_step_buttons[3].click()
    app.processEvents()
    assert window.data_panel.ihc_context_stack.currentIndex() == 3
    assert window.ihc_workspace.canvas_stack.currentWidget() is window.ihc_workspace.layout_canvas
    assert window.ihc_workspace.canvas_tools_group.isVisible()
    assert window.ihc_workspace.workflow_group.isHidden()
    assert not window.data_panel.ihc_stats_run_button.isVisible()

    _close_widget(window, app)


def test_qpcr_startup_size_is_not_forced_by_hidden_module_pages() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    available = QRect(0, 0, 2560, 1392)
    window = _ScreenBoundMainWindow(Path(__file__).resolve().parents[1] / "resources", available)

    window.set_module("qpcr")
    window.show()
    app.processEvents()

    assert window.canvas_stack.minimumSizeHint().height() < 600
    assert window.geometry().height() <= available.height()
    assert window.preview_canvas.paper.height() <= window.preview_canvas.scroll_area.viewport().height() + 2
    viewport = window.preview_canvas.scroll_area.viewport()
    paper = window.preview_canvas.paper
    top_left = viewport.mapFromGlobal(paper.mapToGlobal(QPoint()))
    assert viewport.rect().contains(QRect(top_left, paper.size()))

    _close_widget(window, app)


def test_fluoro_startup_size_is_not_forced_by_hidden_context_pages() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    available = QRect(0, 0, 2560, 1392)
    window = _ScreenBoundMainWindow(Path(__file__).resolve().parents[1] / "resources", available)

    window.set_module("fluoro")
    window.show()
    app.processEvents()

    right_scroll = window.fluoro_workspace.workflow_panel.findChild(QScrollArea, "FluorescenceWorkflowRightScroll")
    assert right_scroll is not None
    assert right_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert window.fluoro_workspace.workflow_panel.findChild(QScrollArea, "FluorescenceWorkflowStepScroll") is None

    for step in range(len(window.fluoro_workspace.step_buttons)):
        window.fluoro_workspace.set_step(step)
        app.processEvents()
        assert window.geometry().height() <= available.height()
        assert window.fluoro_workspace.minimumSizeHint().height() < 700
        assert not isinstance(window.fluoro_workspace.context_stack.currentWidget(), QScrollArea)
        assert not window.fluoro_workspace.context_stack.currentWidget().findChildren(QScrollArea)
        assert window.fluoro_workspace.active_image_group.isHidden()

    _close_widget(window, app)


def test_science_workflow_right_panels_do_not_use_nested_step_scroll_boxes() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    module_specs = [
        ("qpcr", lambda: window.set_qpcr_workflow_step(2), window.qpcr_workflow_panel, "QPCRWorkflowRightScroll", "QPCRWorkflowStepScroll"),
        ("wb", lambda: window.set_wb_workflow_step(5), window.wb_workflow_panel, "WBWorkflowRightScroll", "WBWorkflowStepScroll"),
        ("fluoro", lambda: window.fluoro_workspace.set_step(4), window.fluoro_workspace.workflow_panel, "FluorescenceWorkflowRightScroll", "FluorescenceWorkflowStepScroll"),
        ("ihc", lambda: window.set_ihc_workflow_step(1), window.ihc_workspace.workflow_panel, "IHCWorkflowRightScroll", "IHCWorkflowStepScroll"),
        ("brightfield", lambda: window.set_brightfield_workflow_step(2), window.brightfield_workspace.workflow_panel, "BrightfieldWorkflowRightScroll", "BrightfieldWorkflowStepScroll"),
    ]

    for module_id, configure_step, workflow_panel, right_scroll_name, step_scroll_name in module_specs:
        window.set_module(module_id)
        configure_step()
        app.processEvents()
        right_scroll = workflow_panel.findChild(QScrollArea, right_scroll_name)
        assert right_scroll is not None
        assert right_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert right_scroll.horizontalScrollBar().maximum() == 0
        assert workflow_panel.findChild(QScrollArea, step_scroll_name) is None

    _close_widget(window, app)


def test_fluoro_measure_mode_buttons_are_equal_height_and_right_panel_fits_layout_step() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1366, 900)
    window.set_module("fluoro")
    window.fluoro_workspace.set_step(2)
    window.show()
    app.processEvents()

    heights = {button.height() for button in window.fluoro_workspace.measure_mode_buttons.values()}
    assert len(heights) == 1

    window.fluoro_workspace.set_step(4)
    app.processEvents()
    right_scroll = window.fluoro_workspace.workflow_panel.findChild(QScrollArea, "FluorescenceWorkflowRightScroll")
    assert right_scroll is not None
    assert right_scroll.horizontalScrollBar().maximum() == 0

    _close_widget(window, app)


def test_all_module_canvases_fit_and_zoom(tmp_path, desktop_screen) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1366, 768)
    window.show()
    app.processEvents()

    window.set_module("qpcr")
    window.preview_canvas.fit_to_canvas()
    qpcr_fit_zoom = window.preview_canvas._zoom_factor
    window.preview_canvas.zoom_by(ZOOM_STEP)
    assert not window.preview_canvas._fit_mode
    assert window.preview_canvas._zoom_factor > qpcr_fit_zoom
    window.preview_canvas.fit_to_canvas()
    assert window.preview_canvas._fit_mode

    window.set_module("wb")
    blot_path = tmp_path / "large_blot.png"
    blot = QImage(1200, 800, QImage.Format.Format_RGB32)
    blot.fill(0xFFDDDDDD)
    assert blot.save(str(blot_path))
    window.import_wb_image(str(blot_path))
    window.wb_canvas.fit_to_canvas()
    assert _scene_rect_fits_view(window.wb_canvas.view, window.wb_canvas._view_controller.backend.content_rect())
    wb_fit_zoom = window.wb_canvas._zoom_factor
    window.wb_canvas.zoom_by(ZOOM_STEP)
    assert not window.wb_canvas._fit_mode
    assert window.wb_canvas._zoom_factor > wb_fit_zoom

    window.set_module("fluoro")
    window.fluoro_workspace.canvas.fit_to_canvas()
    assert _scene_rect_fits_view(window.fluoro_workspace.canvas.view, window.fluoro_workspace.canvas._view_controller.backend.content_rect())
    fluoro_fit_zoom = window.fluoro_workspace.canvas._zoom_factor
    window.fluoro_workspace.canvas.zoom_by(ZOOM_STEP)
    assert not window.fluoro_workspace.canvas._fit_mode
    assert window.fluoro_workspace.canvas._zoom_factor > fluoro_fit_zoom

    window.set_module("ihc")
    ihc_path = _write_synthetic_ihc_png(tmp_path / "ihc-fit.png")
    window.import_ihc_images([str(ihc_path)])
    window.ihc_workspace.canvas.fit_to_canvas()
    ihc_fit_zoom = window.ihc_workspace.canvas._zoom_factor
    window.ihc_workspace.canvas.zoom_by(ZOOM_STEP)
    assert not window.ihc_workspace.canvas._fit_mode
    assert window.ihc_workspace.canvas._zoom_factor > ihc_fit_zoom

    window.set_module("composer")
    window.composer_canvas.fit_to_canvas()
    assert _scene_rect_fits_view(window.composer_canvas.view, window.composer_canvas._fit_target_rect())
    composer_fit_zoom = window.composer_canvas._zoom_factor
    window.composer_canvas.zoom_by(ZOOM_STEP)
    assert not window.composer_canvas._fit_mode
    assert window.composer_canvas._zoom_factor > composer_fit_zoom
    assert _scene_rect_centered_in_composer_visible_area(window.composer_canvas, window.composer_canvas.paper_rect)

    _close_widget(window, app)


def test_composer_asset_shelf_uses_sidebar_without_opening_dialog(desktop_screen) -> None:
    app=QApplication.instance() or QApplication([])
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window.resize(1366,768);window.show();window.set_module('composer')
    for _ in range(8):app.processEvents()
    workspace=window.composer_workspace
    before=workspace.canvas.view.size()
    assert not workspace.asset_dialog.isVisible()
    assert window.data_panel.isHidden()
    workspace.assets_button.click();app.processEvents()
    assert not workspace.asset_dialog.isVisible()
    assert workspace.assets.isVisibleTo(workspace.sidebar)
    assert workspace.canvas.view.size()==before
    workspace.show_inspector();app.processEvents()
    assert workspace.canvas.view.size()==before
    _close_widget(window,app)


def test_composer_v9_shell_is_canvas_first_at_laptop_viewport(desktop_screen) -> None:
    app=QApplication.instance() or QApplication([])
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window.resize(1366,768);window.show();window.set_module('composer')
    window.composer_canvas.fit_to_canvas()
    for _ in range(5):app.processEvents()
    workspace=window.composer_workspace
    assert window.data_panel.isHidden()
    assert not window.v9_shell.is_right_panel_collapsed()
    assert workspace.assets_button.isVisibleTo(workspace)
    assert not workspace.asset_dialog.isVisible()
    assert workspace.canvas.view.viewport().height()>=520
    assert _scene_rect_fits_view(workspace.canvas.view,workspace.canvas._fit_target_rect())
    _close_widget(window,app)


def test_composer_landscape_startup_stays_centered_after_zoom(desktop_screen) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1920, 1080)
    window.set_module("composer")
    for panel in window.project.panels[:4]:
        if not window.composer_canvas.is_panel_placed(panel.panel_uuid):
            window.composer_canvas.place_panel(panel.panel_uuid)
    window.show()
    for _ in range(20):
        app.processEvents()

    assert _scene_rect_fits_view(window.composer_canvas.view, window.composer_canvas._fit_target_rect())
    assert _scene_rect_centered_in_composer_visible_area(window.composer_canvas, window.composer_canvas.paper_rect)

    window.composer_canvas.zoom_by(ZOOM_STEP)
    app.processEvents()
    assert not window.composer_canvas._fit_mode
    assert _scene_rect_centered_in_composer_visible_area(window.composer_canvas, window.composer_canvas.paper_rect)

    window.resize(1920, 1200)
    for _ in range(10):
        app.processEvents()
    assert not window.composer_canvas._fit_mode
    assert _scene_rect_centered_in_composer_visible_area(window.composer_canvas, window.composer_canvas.paper_rect)

    window.composer_canvas.zoom_by(1 / ZOOM_STEP)
    app.processEvents()
    assert _scene_rect_centered_in_composer_visible_area(window.composer_canvas, window.composer_canvas.paper_rect)

    _close_widget(window, app)
