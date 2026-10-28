from __future__ import annotations

import os
from pathlib import Path
from string import Formatter

from PyQt6.QtGui import QAction, QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QGroupBox, QLabel, QPushButton

import ui.main_window as main_window_module
import ui.widgets.editable_text_item as editable_text_item_module
from core.utils.i18n import TRANSLATIONS, set_language
from ui.dialogs.wb_export_dialog import WBExportDialog
from ui.main_window import MainWindow
from ui.widgets.canvas_zoom import CanvasZoomControls
from ui.widgets.editable_text_item import EditableMovableTextItem


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _pixmap(color: str) -> QPixmap:
    pixmap = QPixmap(12, 10)
    pixmap.fill(QColor(color))
    return pixmap


def test_fluoro_backend_hint_follows_language_without_changing_method():
    from core.utils.i18n import tr,get_language
    app = _app()
    previous_language = get_language()
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    try:
        workspace = window.fluoro_workspace
        for backend in ('local_heuristic', 'cellpose_cyto3'):
            window.switch_language('en')
            workspace.set_ai_analysis_settings({'backend': backend})
            window.switch_language('zh')
            app.processEvents()
            key = 'cellpose_area_hint' if backend == 'cellpose_cyto3' else 'local_area_hint'
            assert workspace.ai_status_label.text() == tr(key)
            assert workspace.ai_analysis_settings()['backend'] == backend
    finally:
        window._capture_clean_project_baseline()
        window.close()
        set_language(previous_language)
        app.processEvents()


def test_translation_catalog_has_exact_parity_and_no_blank_values() -> None:
    assert set(TRANSLATIONS["en"]) == set(TRANSLATIONS["zh"])
    assert all(str(value).strip() for values in TRANSLATIONS.values() for value in values.values())
    for key in TRANSLATIONS["en"]:
        english_fields = sorted(
            (field, spec)
            for _, field, spec, _ in Formatter().parse(TRANSLATIONS["en"][key])
            if field is not None
        )
        chinese_fields = sorted(
            (field, spec)
            for _, field, spec, _ in Formatter().parse(TRANSLATIONS["zh"][key])
            if field is not None
        )
        assert english_fields == chinese_fields, key
    assert TRANSLATIONS["zh"]["v9_module_stylelab"] == "风格实验室"
    assert TRANSLATIONS["zh"]["v9_stage_stylelab"] == "样式 - 样式库与本地提取"
    assert TRANSLATIONS["zh"]["style_sidebar_experimental"] == "实验性预设"
    assert TRANSLATIONS["en"]["v9_module_viewer"] == "Image Browser"
    assert TRANSLATIONS["zh"]["v9_module_viewer"] == "图片浏览"
    assert TRANSLATIONS["en"]["viewer_open_folder"] == "Open Folder"
    assert TRANSLATIONS["zh"]["viewer_open_folder"] == "打开文件夹"
    assert TRANSLATIONS["en"]["viewer_native_png"] == "Native PNG"
    assert TRANSLATIONS["zh"]["viewer_native_png"] == "原分辨率 PNG"
    assert TRANSLATIONS["en"]["viewer_empty"] == "Open a folder to browse images"
    assert TRANSLATIONS["zh"]["viewer_empty"] == "打开文件夹以浏览图片"

    for key in (
        "viewer_line_width",
        "viewer_line_color",
        "viewer_text_color",
        "viewer_font",
        "viewer_font_size_px",
        "viewer_bold",
        "viewer_italic",
        "viewer_scale_bar_drag_hint",
        "viewer_send_to_fluorescence",
        "viewer_transfer_preparing",
        "viewer_transfer_failed",
    ):
        assert TRANSLATIONS["en"][key].strip()
        assert TRANSLATIONS["zh"][key].strip()


def test_canvas_zoom_fit_button_retranslates_text_and_tooltip() -> None:
    app = _app()
    set_language("en")
    controls = CanvasZoomControls()
    try:
        assert controls.fit_button.text() == "Fit"
        assert controls.fit_button.toolTip() == "Show the full canvas"
        assert controls.fit_button.property("i18nKey") == "zoom_fit"

        set_language("zh")
        controls.retranslate()
        app.processEvents()

        assert controls.fit_button.text() == "全图"
        assert controls.fit_button.toolTip() == "显示完整画布"

        set_language("en")
        controls.retranslate()
        app.processEvents()

        assert controls.fit_button.text() == "Fit"
        assert controls.fit_button.toolTip() == "Show the full canvas"
    finally:
        controls.close()
        controls.deleteLater()
        app.processEvents()
        set_language("en")


def test_main_window_switches_representative_ui_for_every_module() -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.switch_language("zh")
        app.processEvents()

        assert window.data_panel.qpcr_group_table.horizontalHeaderItem(0).text() == "样本"
        assert window.data_panel.wb_strip_table.horizontalHeaderItem(1).text() == "图片"
        assert window.fluoro_workspace.fluoro_stats_chart_toggle.text() == "图表预览"
        assert window.ihc_workspace.remove_button.text() == "移除所选"
        assert window.brightfield_workspace.remove_button.text() == "移除所选"
        assert window.composer_workspace.assets_button.text() == "素材"
        window.stylelab_workspace.open_local_extraction()
        assert window.stylelab_workspace.local_extraction.btn_saved.text() == "已保存样式"
        assert window.preview_canvas.zoom_controls.zoom_in_button.toolTip() == "放大"
        assert window.data_panel.brightfield_he_fiber_draw_scale_line_button.toolTip() == "在当前 H&E 图片上绘制比例线。默认水平，按住 Shift 可任意角度。"
        assert window.data_panel.brightfield_he_fiber_draw_scale_line_button.text() == "绘制参考线校准"
        assert window.data_panel.brightfield_he_fiber_refresh_results_button.text() == "刷新"
        assert window.data_panel.brightfield_he_fiber_reset_scale_button.text() == "重置"
        assert window.data_panel.ihc_results_table.horizontalHeaderItem(2).text() == "阳性面积 %"
        assert window.data_panel.brightfield_ai_results_table.horizontalHeaderItem(3).text() == "H&E 肌纤维计数"
        assert window.data_panel.brightfield_step_buttons[1].text() == "预处理 / 分割"
        assert "对齐" in {
            group.title()
            for group in window.data_panel.wb_context_stack.findChildren(QGroupBox)
        }
        assert window.nature_preset_status_label.text() == "Nature 预设"
        assert window.imagej_status_label.text() == "ImageJ：未连接"

        window.switch_language("en")
        app.processEvents()
        assert window.data_panel.qpcr_group_table.horizontalHeaderItem(0).text() == "Sample"
        assert window.stylelab_workspace.local_extraction.btn_saved.text() == "Saved styles"
        assert window.data_panel.ihc_results_table.horizontalHeaderItem(2).text() == "Positive Area %"
        assert window.data_panel.brightfield_ai_results_table.horizontalHeaderItem(3).text() == "H&E Fiber Count"
        assert window.data_panel.brightfield_step_buttons[1].text() == "Preprocess/Segment"
        assert window.data_panel.brightfield_he_fiber_refresh_results_button.text() == "Refresh"
        assert window.data_panel.brightfield_he_fiber_reset_scale_button.text() == "Reset"
    finally:
        window.switch_language("en")
        window._capture_clean_project_baseline()
        window.close()
        window.deleteLater()
        app.processEvents()
        set_language("en")


def test_language_switch_retranslates_final_visible_residuals_without_rebuilding_menus() -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.set_module("stylelab")
        workspace = window.stylelab_workspace
        menu_actions_before = window.findChildren(QAction)
        menu_ids_before = [id(menu) for menu in window._top_menus.values()]
        file_action_ids_before = [id(action) for action in window._file_menu_actions.values()]
        file_shortcuts_before = [
            action.shortcut().toString()
            for action in window._file_menu_actions.values()
        ]
        action_count_before = len(menu_actions_before)
        preview_mode_before = workspace.local_extraction.preview_board.chart()
        name_before = workspace.local_extraction.name_edit.text()

        window.switch_language("zh")
        app.processEvents()

        assert [menu.title() for menu in window._top_menus.values()] == [
            "文件",
            "编辑",
            "工具",
            "预设",
            "ImageJ",
            "语言",
            "帮助",
        ]
        assert [action.text() for action in window._file_menu_actions.values()] == [
            "新建项目",
            "保存 .sfig",
            "打开 .sfig",
        ]
        assert [id(menu) for menu in window._top_menus.values()] == menu_ids_before
        assert [id(action) for action in window._file_menu_actions.values()] == file_action_ids_before
        assert [
            action.shortcut().toString()
            for action in window._file_menu_actions.values()
        ] == file_shortcuts_before
        assert len(window.findChildren(QAction)) == action_count_before
        assert workspace.local_extraction.preview_board.chart() == preview_mode_before
        assert workspace.local_extraction.name_edit.text() == name_before

        right_hide_button = window.findChild(QPushButton, "V9RightPanelHideButton")
        assert right_hide_button is not None
        assert right_hide_button.toolTip() == "隐藏工作流面板"
        assert workspace.local_extraction.btn_saved.text() == "已保存样式"
        assert not workspace.findChildren(QLabel, "StyleLabFormLabel")

        window.switch_language("en")
        app.processEvents()

        assert [menu.title() for menu in window._top_menus.values()] == [
            "File",
            "Edit",
            "Tools",
            "Presets",
            "ImageJ",
            "Language",
            "Help",
        ]
        assert [action.text() for action in window._file_menu_actions.values()] == [
            "New Project",
            "Save .sfig",
            "Open .sfig",
        ]
        assert [
            action.shortcut().toString()
            for action in window._file_menu_actions.values()
        ] == file_shortcuts_before
        assert len(window.findChildren(QAction)) == action_count_before
        assert right_hide_button.toolTip() == "Hide workflow panel"
        assert workspace.local_extraction.btn_saved.text() == "Saved styles"
    finally:
        window.switch_language("en")
        window._capture_clean_project_baseline()
        window.close()
        window.deleteLater()
        app.processEvents()
        set_language("en")


def test_runtime_status_dialog_and_file_caption_follow_language(monkeypatch) -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    file_dialog_calls: list[tuple[str, str]] = []
    message_box_calls: list[tuple[str, str]] = []

    def fake_open_file_name(_parent, title: str, _directory: str, file_filter: str):
        file_dialog_calls.append((title, file_filter))
        return "", ""

    def fake_information(_parent, title: str, message: str):
        message_box_calls.append((title, message))
        return None

    monkeypatch.setattr(main_window_module.QFileDialog, "getOpenFileName", fake_open_file_name)
    monkeypatch.setattr(main_window_module.QMessageBox, "information", fake_information)

    try:
        window.switch_language("zh")
        app.processEvents()

        window.set_module("qpcr")
        window.import_active_module()
        assert file_dialog_calls[-1] == (
            "导入 qPCR 数据",
            "qPCR data (*.csv *.tsv *.txt *.xlsx *.xls)",
        )

        window.set_module("stylelab")
        window.import_active_module()
        assert message_box_calls[-1] == (
            "导入",
            "当前模块的导入流程将在后续阶段提供。",
        )

        window.ihc_analysis_controller.run_analysis()
        assert message_box_calls[-1] == (
            "组织学 / IHC",
            "运行分析前请先导入组织学 / IHC 图片。",
        )

        window.apply_style_lab_runtime_saved_preset()
        assert window.statusBar().currentMessage() == "科学渲染预览正在使用内置样式预设"

        window.data_panel.populate_ihc_measurements(
            [
                {
                    "measurement_uuid": "runtime-summary",
                    "image_name": "sample.png",
                    "results": {
                        "positive_area_percent": 12.5,
                        "mean_dab_intensity": 0.25,
                        "h_score": 80.0,
                    },
                    "reliability": {"status": "PASS"},
                }
            ]
        )
        assert window.data_panel.ihc_summary_label.text().startswith("最新 IHC 结果")

        window.switch_language("en")
        window.data_panel.populate_ihc_measurements(
            [
                {
                    "measurement_uuid": "runtime-summary",
                    "image_name": "sample.png",
                    "results": {
                        "positive_area_percent": 12.5,
                        "mean_dab_intensity": 0.25,
                        "h_score": 80.0,
                    },
                    "reliability": {"status": "PASS"},
                }
            ]
        )
        assert window.data_panel.ihc_summary_label.text().startswith("Latest IHC result")
    finally:
        window.switch_language("en")
        window._capture_clean_project_baseline()
        window.close()
        window.deleteLater()
        app.processEvents()
        set_language("en")


def test_editable_text_input_dialog_uses_current_language(monkeypatch) -> None:
    app = _app()
    set_language("zh")
    captured: dict[str, str] = {}
    item = EditableMovableTextItem(
        "Label",
        text_callback=lambda *_args: None,
        moved_callback=lambda *_args: None,
        style_callback=lambda *_args: None,
    )

    def fake_menu_exec(menu, _position):
        for action in menu.actions():
            submenu = action.menu()
            if submenu is None:
                continue
            for child_action in submenu.actions():
                if child_action.text() == "Custom...":
                    return child_action
        return None

    def fake_get_double(_parent, title, prompt, value, *_args):
        captured["title"] = title
        captured["prompt"] = prompt
        return value, False

    class FakeContextMenuEvent:
        @staticmethod
        def screenPos():
            return None

    monkeypatch.setattr(editable_text_item_module.QMenu, "exec", fake_menu_exec)
    monkeypatch.setattr(editable_text_item_module.QInputDialog, "getDouble", fake_get_double)

    try:
        item.contextMenuEvent(FakeContextMenuEvent())
        assert captured == {"title": "文字大小", "prompt": "字号（pt）"}
    finally:
        app.processEvents()
        set_language("en")


def test_language_switch_preserves_workflow_and_preview_modes() -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.set_module("ihc")
        window.data_panel.set_ihc_step(1, emit_signal=False)
        window.ihc_workspace.set_step(1)
        window.data_panel.ihc_active_image_combo.blockSignals(True)
        window.data_panel.ihc_active_image_combo.addItem("IHC image", "ihc-image")
        window.data_panel.ihc_active_image_combo.blockSignals(False)
        window.ihc_workspace.canvas.set_pixmap(_pixmap("#AA0000"), title="Histology / IHC preview")
        window.ihc_workspace.canvas.set_pixmap(
            _pixmap("#00AA00"), title="Histology / IHC analysis overlay"
        )

        window.switch_language("zh")
        app.processEvents()

        assert window.active_module == "ihc"
        assert window.data_panel.ihc_context_stack.currentIndex() == 1
        assert window.data_panel.ihc_active_image_combo.currentData() == "ihc-image"
        assert window.ihc_workspace.current_preview_mode() == "overlay"

        window.set_module("brightfield")
        window.data_panel.set_brightfield_step(2, emit_signal=False)
        window.brightfield_workspace.set_step(2)
        window.brightfield_workspace.canvas.set_display_state(
            _pixmap("#1111AA"), title="Brightfield preview"
        )
        window.brightfield_workspace.canvas.set_display_state(
            _pixmap("#AA11AA"), title="Brightfield segmentation overlay"
        )

        window.switch_language("en")
        app.processEvents()

        assert window.active_module == "brightfield"
        assert window.data_panel.brightfield_context_stack.currentIndex() == 2
        assert window.brightfield_workspace.current_preview_mode() == "overlay"

        window.set_module("fluoro")
        window.fluoro_workspace.set_step(2, refresh=False)
        window.fluoro_workspace._sync_ai_overlay_buttons(has_overlay=True, show_overlay=True)

        window.switch_language("zh")
        app.processEvents()

        assert window.active_module == "fluoro"
        assert window.fluoro_workspace.context_stack.currentIndex() == 2
        assert window.fluoro_workspace.ai_overlay_preview_button.isChecked()
        assert window.fluoro_workspace.canvas._measurement_overlay_visible
    finally:
        window.switch_language("en")
        window._capture_clean_project_baseline()
        window.close()
        window.deleteLater()
        app.processEvents()
        set_language("en")


def test_wb_export_dialog_uses_current_chinese_language() -> None:
    app = _app()
    set_language("zh")
    dialog = WBExportDialog()
    try:
        assert dialog.windowTitle() == "导出 WB 图"
        assert dialog.format_label.text() == "格式"
        assert dialog.dpi_label.text() == "DPI"
        assert dialog.pdf_radio.text() == "PDF（矢量，推荐）"
        assert dialog.include_source_checkbox.text() == "包含源数据（完整 Blot + 日志）"
        assert dialog.button_box.button(QDialogButtonBox.StandardButton.Ok).text() == "确定"
        assert dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel).text() == "取消"
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        set_language("en")
