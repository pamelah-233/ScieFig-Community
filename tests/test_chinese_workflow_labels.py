from copy import deepcopy
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QGroupBox

from core.utils.i18n import TRANSLATIONS, set_language, tr


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    for font in ('msyh.ttc', 'segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + font)
    previous_style = instance.styleSheet()
    instance.setStyleSheet((Path(__file__).resolve().parents[1] / 'resources/sciefig.qss').read_text(encoding='utf-8'))
    set_language('en')
    yield instance
    instance.setStyleSheet(previous_style)
    set_language('en')


def test_detached_fluorescence_workflow_retranslates_controls_headers_and_saved_results(app):
    from core.models import FluorescencePanel
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace

    workspace = FluorescenceWorkspace()
    panel = FluorescencePanel(panel_id='zh-labels')
    row = dict(count_uuid='count-a', image_uid='image-a', channel_name='DAPI', count=7,
               total_positive_area_px=123, mean_object_area_px=17.5, mean_intensity=200)
    panel.data['fluorescence_images'] = [dict(uid='image-a', source_name='My English sample.tif',
        metadata={}, channels=[], object_counts=[row])]
    panel.data['active_image_uid'] = 'image-a'
    panel.analysis['object_counts'] = [row]
    workspace.set_panel(panel, refresh=False)
    workspace._populate_count_results()
    original = deepcopy(panel.to_dict())
    workspace._set_measure_mode('spot_count')
    workspace.context_stack.setCurrentIndex(2)

    set_language('zh')
    workspace.retranslate()
    assert workspace.sample_spot_button.text() == tr('sample_spot_tool')
    assert workspace.clear_counts_button.text() == tr('clear_counts')
    assert workspace.measure_mode_buttons['spot_count'].text() == tr('spot_count')
    assert workspace.count_table.horizontalHeaderItem(0).text() == '通道'
    assert workspace.count_table.horizontalHeaderItem(1).text() == '计数'
    assert '7' == workspace.count_table.item(0, 1).text()
    assert workspace.count_table.item(0, 0).text() == 'DAPI'
    assert workspace.count_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == 'count-a'
    assert '传统' in workspace.count_table.item(0, 5).text()
    assert '1' in workspace.spot_count_summary_label.text()
    assert '计数历史' in workspace.spot_count_summary_label.text()
    assert workspace.measure_mode == 'spot_count'
    assert workspace.context_stack.currentIndex() == 2
    assert panel.to_dict() == original
    set_language('en')
    workspace.retranslate()
    assert workspace.count_table.horizontalHeaderItem(0).text() == 'Channel'
    assert workspace.count_table.item(0, 0).text() == 'DAPI'
    assert panel.to_dict() == original
    workspace.workflow_panel.close()
    workspace.close()


def test_fluorescence_form_labels_and_background_choice_localize_without_changing_values(app):
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace

    workspace = FluorescenceWorkspace()
    workspace.roi_background_method_combo.setCurrentIndex(2)
    set_language('zh')
    workspace.retranslate()
    labels = {label.text() for label in workspace.workflow_panel.findChildren(QLabel)}
    assert {'格式', '尺寸', '位深', '像素大小', '物镜'} <= labels
    assert workspace.roi_background_method_combo.currentText() == tr('fixed_value_per_channel')
    assert workspace.roi_background_method_combo.currentData() == 'Fixed value per channel'
    assert workspace.ai_results_table.horizontalHeaderItem(0).text() == '对象 ID'
    assert workspace.ai_summary_stats_table.horizontalHeaderItem(0).text() == '通道'
    assert workspace.fluoro_stats_group_table.horizontalHeaderItem(0).text() == tr('sample')
    workspace.workflow_panel.close()
    workspace.close()


def test_detached_histology_modes_retranslate_without_mode_or_metric_changes(app):
    from ui.panels.placeholder_panel import DataInputPanel

    panel = DataInputPanel()
    panel.ihc_context_stack.setParent(None)
    panel.brightfield_context_stack.setParent(None)
    panel.ihc_mode_combo.setCurrentIndex(panel.ihc_mode_combo.findData('assisted'))
    panel.brightfield_segmentation_mode_combo.setCurrentIndex(panel.brightfield_segmentation_mode_combo.findData('lab'))
    set_language('zh')
    panel.retranslate()
    assert panel.ihc_mode_combo.currentData() == 'assisted'
    assert panel.ihc_mode_combo.currentText() == tr('assisted')
    assert panel.brightfield_segmentation_mode_combo.currentData() == 'lab'
    assert panel.brightfield_segmentation_mode_combo.currentText() == 'LAB 颜色范围'
    assert not any(group.title() == 'Calibration & presets' for group in panel.ihc_context_stack.findChildren(QGroupBox))
    panel.ihc_context_stack.close()
    panel.brightfield_context_stack.close()
    panel.close()


def test_canvas_hint_and_scale_appearance_translation_keys_exist():
    for key in ('fluoro_canvas_import_hint', 'fluoro_canvas_reload_hint', 'fluoro_canvas_merge',
                'fluoro_canvas_channel', 'scale_bar_font', 'scale_bar_font_size', 'scale_bar_line', 'scale_bar_line_color'):
        assert key in TRANSLATIONS['en']
        assert key in TRANSLATIONS['zh']
        assert TRANSLATIONS['en'][key] != TRANSLATIONS['zh'][key]


@pytest.mark.parametrize('module', ['ihc', 'brightfield'])
def test_histology_layout_controls_retranslate_in_detached_workflow_at_narrow_width(app, module):
    from ui.panels.brightfield_workspace import BrightfieldWorkspace
    from ui.panels.ihc_workspace import IHCWorkspace

    workspace = (IHCWorkspace if module == 'ihc' else BrightfieldWorkspace)(QLabel('context'))
    workspace.workflow_panel.resize(320, 720)
    workspace.workflow_panel.show()
    workspace.set_step(4)
    set_language('zh')
    workspace.retranslate()
    app.processEvents()
    labels = {label.text() for label in workspace.text_format_controls_panel.findChildren(QLabel)}
    assert {'字体', '大小', '样式', '旋转'} <= labels
    assert workspace.scale_bar_text_position_combo.currentText() == tr('above')
    assert workspace.scale_bar_text_position_combo.currentData() == 'above'
    for button in workspace.workflow_panel.findChildren(QPushButton):
        if button.isVisible() and not button.visibleRegion().isEmpty() and button.text():
            assert button.fontMetrics().horizontalAdvance(button.text()) <= button.contentsRect().width() + 4
    workspace.workflow_panel.close()
    workspace.close()


def test_histology_metric_labels_localize_but_analysis_identifiers_remain_unchanged(app):
    from ui.panels.placeholder_panel import DataInputPanel

    panel = DataInputPanel()
    panel.ihc_stats_metric_combo.setCurrentText('Mean DAB Intensity')
    panel.ihc_quant_goal_combo.setCurrentText('Positive Area %')
    panel.brightfield_stats_metric_combo.setCurrentText('ROI Mean Intensity')
    set_language('zh')
    panel.retranslate()
    assert panel.current_ihc_stats_metric() == 'Mean DAB Intensity'
    assert panel.current_brightfield_stats_metric() == 'ROI Mean Intensity'
    assert panel.ihc_stats_metric_combo.currentText() == '平均 DAB 强度'
    assert panel.brightfield_stats_metric_combo.currentText() == 'ROI 平均强度'
    assert panel.current_ihc_quantification_goal() == 'Positive Area %'
    assert '阳性面积' in panel.ihc_quant_goal_combo.currentText()
    assert panel.brightfield_mode_combo.itemText(0) == '简易'
    panel.close()


def test_localized_fluorescence_selectors_keep_raw_view_layout_and_scale_values(app):
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace

    workspace = FluorescenceWorkspace()
    workspace.view_mode_combo.setCurrentIndex(1)
    workspace.layout_mode_combo.setCurrentIndex(2)
    workspace.scale_bar_text_position_combo.setCurrentIndex(1)
    workspace.fluoro_stats_source_combo.setCurrentIndex(1)
    set_language('zh')
    workspace.retranslate()
    assert workspace.view_mode_combo.currentData() == 'split'
    assert workspace.view_mode_combo.currentText() == '分通道显示'
    assert workspace.layout_mode_combo.currentData() == 'batch_grid'
    assert workspace.layout_mode_combo.currentText() == '批量网格'
    assert workspace.scale_bar_text_position_combo.currentData() == 'below'
    assert workspace.scale_bar_text_position_combo.currentText() == tr('below')
    assert workspace.fluoro_stats_source_combo.currentData() == 'object_count'
    assert workspace.fluoro_stats_source_combo.currentText() == tr('object_count')
    assert workspace.fluoro_stats_metric_combo.currentData() == 'count'
    assert workspace.fluoro_stats_metric_combo.currentText() == '计数'
    workspace.workflow_panel.close()
    workspace.close()


def test_review_polygon_error_is_clear_chinese_and_keeps_original_record(app):
    from PyQt6.QtGui import QImage
    from ui.dialogs.object_review_dialog import ObjectReviewDialog
    from tests.test_measurement_review import fluorescence_record

    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.black)
    original = fluorescence_record()
    snapshot = deepcopy(original)
    dialog = ObjectReviewDialog(original, image, language='zh')
    dialog._finish_drawing('add', [[-1, 0], [8, 0], [8, 8]])
    assert '源图像范围内' in dialog.status_label.text()
    assert 'Polygon' not in dialog.status_label.text()
    assert original == snapshot
    assert not dialog.session.changed
    dialog.close()


def test_batch_failure_displays_chinese_without_rewriting_raw_error_provenance(app, tmp_path):
    from PyQt6.QtCore import QSettings
    from core.batch_analysis import BatchImageResult
    from core.utils.calibration_presets import CalibrationPresets
    from ui.dialogs.batch_analysis_dialog import BatchAnalysisDialog
    from tests.test_batch_analysis import fluorescence_panel

    set_language('zh')
    panel = fluorescence_panel()
    library = CalibrationPresets(QSettings(str(tmp_path / 'presets.ini'), QSettings.Format.IniFormat))
    dialog = BatchAnalysisDialog(panel, 'count_channel_objects', {'channel_indices': [0]}, library=library)
    error = 'Analysis returned no measurement records; the backend may be unavailable.'
    result = BatchImageResult('image-0', 'failed', {}, error=error)
    dialog._receive_result(result)
    assert '没有返回测量记录' in dialog.image_table.item(0, 3).text()
    assert result.error == error
    assert not panel.analysis.get('object_counts')
    dialog.close()
    panel.dispose()


@pytest.mark.parametrize('width', [320, 480])
def test_fluorescence_chinese_workflow_buttons_fit_narrow_panels(app, width):
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace

    workspace = FluorescenceWorkspace()
    workspace.workflow_panel.resize(width, 900)
    workspace.workflow_panel.show()
    workspace.set_step(2)
    set_language('zh')
    workspace.retranslate()
    app.processEvents()
    assert workspace.workflow_panel.width() == width
    for button in workspace.workflow_panel.findChildren(QPushButton):
        if button.isVisible() and not button.visibleRegion().isEmpty() and button.text():
            needed = max(button.fontMetrics().horizontalAdvance(line) for line in button.text().splitlines())
            assert needed <= button.contentsRect().width() + 4, button.text()
    workspace.workflow_panel.close()
    workspace.close()


def test_fluorescence_workspace_language_switch_updates_generated_canvas_hints(app):
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace

    workspace = FluorescenceWorkspace()
    workspace.canvas.refresh()
    set_language('zh')
    workspace.retranslate()
    texts = [item.toPlainText() for item in workspace.canvas.scene.items() if hasattr(item, 'toPlainText')]
    assert tr('fluoro_canvas_import_hint') in texts
    workspace.workflow_panel.close()
    workspace.close()


def test_language_roundtrip_preserves_statistics_groups_includes_and_reference(app):
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace
    from tests.test_batch_analysis import fluorescence_panel

    workspace = FluorescenceWorkspace()
    panel = fluorescence_panel()
    for uid in ('image-0', 'image-1'):
        panel.count_channel_objects(uid, [0])
    workspace.set_panel(panel, refresh=False)
    workspace.fluoro_stats_source_combo.setCurrentIndex(workspace.fluoro_stats_source_combo.findData('object_count'))
    table = workspace.fluoro_stats_group_table
    table.item(0, 1).setText('Control')
    table.item(1, 1).setText('Treated')
    table.cellWidget(0, 2).setChecked(False)
    workspace._populate_fluoro_stats_controls()
    workspace.fluoro_stats_reference_group_combo.setCurrentText('Treated')
    original = deepcopy(panel.to_dict())
    source = workspace.fluoro_stats_source_combo.currentData()
    metric = workspace.fluoro_stats_metric_combo.currentData()
    for language in ('zh', 'en'):
        set_language(language)
        workspace.retranslate()
        assert workspace.fluoro_stats_reference_group_combo.currentText() == 'Treated'
        assert [table.item(index, 1).text() for index in range(2)] == ['Control', 'Treated']
        assert not table.cellWidget(0, 2).isChecked()
        assert table.cellWidget(1, 2).isChecked()
        assert workspace.fluoro_stats_source_combo.currentData() == source
        assert workspace.fluoro_stats_metric_combo.currentData() == metric
        assert panel.to_dict() == original
    workspace.workflow_panel.close()
    workspace.close()
    panel.dispose()


def test_ai_table_headers_remain_readable_instead_of_squeezing_all_columns(app):
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace

    workspace = FluorescenceWorkspace()
    set_language('zh')
    workspace.retranslate()
    for table in (workspace.ai_results_table, workspace.ai_summary_stats_table):
        table.setParent(None)
        table.resize(360, 180)
        table.show()
        app.processEvents()
        for column in range(table.columnCount()):
            label = table.horizontalHeaderItem(column).text()
            assert table.columnWidth(column) >= table.horizontalHeader().fontMetrics().horizontalAdvance(label) + 16, label
        assert table.horizontalScrollBar().maximum() > 0
        table.close()
    workspace.workflow_panel.close()
    workspace.close()


@pytest.mark.parametrize('error', [KeyError('A selected image is no longer available.'), "'A selected image is no longer available.'"])
def test_known_batch_key_errors_translate_without_general_message_rewriting(app, error):
    from ui.dialogs.batch_analysis_dialog import _error_text

    set_language('zh')
    assert _error_text(error) == '所选图像已不可用。'
    assert _error_text('Vendor-specific error with raw ID abc') == 'Vendor-specific error with raw ID abc'
